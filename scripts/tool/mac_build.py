#!/usr/bin/env python3
"""
🖥️ Mac 本地运行脚本 — 构建所有游戏的向量库（FAISS）

服务器 CPU/RAM 不足（3.6GB），无法跑 fastembed + FAISS。
请在你的 MacBook 上运行此脚本，然后将生成的 vectorstore 目录传回服务器。

用法：
    cd /data/learning/agent
    python3 scripts/tool/mac_build.py --game all          ← 构建所有游戏
    python3 scripts/tool/mac_build.py --game oni           ← 只构建某个游戏
    python3 scripts/tool/mac_build.py --game terraria
    python3 scripts/tool/mac_build.py --game silksong
    python3 scripts/tool/mac_build.py --game hollow_knight
    python3 scripts/tool/mac_build.py --game cyberpunk2077
    python3 scripts/tool/mac_build.py --game va11halla
    python3 scripts/tool/mac_build.py --game mhw
    python3 scripts/tool/mac_build.py --game baldurs_gate3

一键版（拉代码 + 环境自检 + 建库 + 本地检索自检 + 给部署命令）：
    bash scripts/tool/mac_prepare_build.sh --game baldurs_gate3

前置条件：
    pip install -r requirements.txt              # 安装 fastembed 等依赖
    # 如果在大陆，可能需要设置 huggingface 镜像：
    # export HF_ENDPOINT=https://hf-mirror.com

输出目录：
    games/{game}/vectorstore/                     ← 每个游戏独立的向量库

元数据契约（勿删）：
    tools.search_knowledge_base 用 filter={"language": "en"/"zh"} 检索，
    FAISS 会把缺少 language 字段的文档直接过滤掉。因此本脚本产出的每篇文档
    都必须带 language：英文 wiki/结构化数据 = en，中文 wiki = zh。
    漏标会导致线上检索恒为空（返回「知识库未找到」），而普通 similarity_search
    自检发现不了——脚本末尾的 mac_prepare_build.sh 会按真实 filter 路径自检。
"""

import argparse
import re
import sys
from pathlib import Path
from typing import List, Dict

# 保证从任意 cwd 运行都能 import rag_agent（与 ingest_game.py 一致）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_SRC_DIR = _PROJECT_ROOT / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

# 直接复用注册表导出的 GAME_DATA（含 data_path_zh），避免维护第二份配置
from rag_agent.game_registry import GAME_DATA, GAME_DB_MAPPING

# ── 防剧透 P1：act 元数据 ──
# 按幕推进的游戏（BG3）建库时给文档打 act 标签，检索侧用 max_act 过滤。
# 解析不出 act 的文档不写该字段（视为 unrestricted，过滤时放行）。
_ACT_WORD = {"one": 1, "two": 2, "three": 3}


def _extract_act(text: str) -> int | None:
    """从正文/分类文本解析 Act 归属（如 BG3 wiki 的 Act_Two_Locations、act: 2）。

    跨多幕的条目取最小值（最早可见的幕）。
    """
    acts = [_ACT_WORD[m.group(1).lower()]
            for m in re.finditer(r"Act_(One|Two|Three)", text, re.IGNORECASE)]
    acts += [int(m.group(1))
             for m in re.finditer(r"\bact[:：]\s*([123])\b", text, re.IGNORECASE)]
    return min(acts) if acts else None


def load_wiki_documents(filepath: str, language: str = "en") -> List[Dict]:
    """从 wiki_data.md 加载文档列表，并给每篇打上 language 元数据。"""
    path = Path(filepath)
    if not path.exists():
        print(f"  ❌ 找不到数据文件：{filepath}")
        return []

    text = path.read_text(encoding="utf-8")

    # 支持三种分页格式（与 scripts/tool/ingest_game.py 对齐）：
    #   格式 A: # 文档：标题   ← 旧格式（HK/ONI/Terraria 等）
    #   格式 B: ## 标题        ← fetch_wiki.py
    #   格式 C: # 标题         ← silksong / mhw（每页一个一级标题）
    split_a = re.split(r"(?=^# 文档[：:])", text, flags=re.MULTILINE)
    split_b = re.split(r"(?=^##\s+.*(?:\n|$))", text, flags=re.MULTILINE)
    split_c = re.split(r"\n(?=# )", text)

    # 判断使用哪种格式：优先用格式 A，其次 B，再次 C
    chunks_a = [c for c in split_a if c.strip().startswith("# 文档")]
    chunks_b = [c for c in split_b if c.strip().startswith("##") and not c.strip().startswith("###") and not c.strip().startswith("####")]
    # 跳过第一个分块如果它是文件标题（没有 ##）
    if split_b and not split_b[0].strip().startswith("##"):
        split_b = split_b[1:]
        chunks_b = [c for c in split_b if c.strip().startswith("##") and not c.strip().startswith("###") and not c.strip().startswith("####")]
    # 格式 C 的第一个 chunk 是文件级头部（如 "# Silksong Wiki Data"），跳过
    chunks_c = [c for c in split_c[1:] if c.strip().startswith("# ")]

    if chunks_a:
        chunks = split_a
        fmt = "doc"
    elif chunks_b:
        chunks = split_b
        fmt = "h2"
    elif chunks_c:
        chunks = chunks_c
        fmt = "h1"
    else:
        # 兜底：整篇作为单文档，至少不让 wiki 内容凭空消失
        print("  ℹ️ 未识别分页格式，整篇作为单文档")
        chunks = [text]
        fmt = "whole"

    docs = []

    for chunk in chunks:
        chunk = chunk.strip()
        if not chunk:
            continue

        if fmt == "doc":
            if not chunk.startswith("# 文档"):
                continue
            lines = chunk.split("\n")
            title_match = re.search(r"^#\s*文档[：:]\s*(.*)", lines[0]) if lines else None
            title = title_match.group(1).strip() if title_match else "Unknown"

            category = ""
            for line in lines[:6]:
                cat_match = re.search(r"- 类别[：:]\s*(.*)", line)
                if cat_match:
                    category = cat_match.group(1).strip()
                    break

            content_lines = []
            for line in lines:
                if any(line.startswith(p) for p in ("# 文档", "- 类别", "- 标识", "- 来源", "- 路径")):
                    continue
                content_lines.append(line)

            content = "\n".join(content_lines).strip()
        elif fmt == "h2":
            # 格式 B: ## 标题 \n ...（直到下一个 ## 或文件结尾）
            lines = chunk.split("\n")
            title_match = re.search(r"^##\s+(.*)", lines[0])
            title = title_match.group(1).strip() if title_match else "Unknown"
            category = ""
            content_lines = lines[1:]  # 跳过标题行
            # 去掉末尾的 --- 分隔线
            while content_lines and content_lines[-1].strip() in ("---", ""):
                content_lines = content_lines[:-1]
            content = "\n".join(content_lines).strip()
        elif fmt == "h1":
            # 格式 C: # 标题 \n ...（silksong / mhw）
            lines = chunk.split("\n")
            title_match = re.search(r"^#\s+(.*)", lines[0])
            title = title_match.group(1).strip() if title_match else "Unknown"
            category = ""
            content_lines = lines[1:]
            while content_lines and content_lines[-1].strip() in ("---", ""):
                content_lines = content_lines[:-1]
            content = "\n".join(content_lines).strip()
        else:
            # 格式 D: 无法识别，整篇一个文档；标题取第一个一级标题
            title_match = re.search(r"^#\s+(.+)", chunk, flags=re.MULTILINE)
            title = title_match.group(1).strip() if title_match else "Unknown"
            category = ""
            lines = [l for l in chunk.split("\n") if not l.startswith("# ")]
            content = "\n".join(lines).strip()

        if content:
            docs.append({
                "content": content,
                "metadata": {"title": title, "category": category,
                             "language": language},
            })

    # 防剧透 P1：从正文/分类文本解析 act 归属（BG3 wiki 分类自带
    # Act_One/Two/Three_* 与 act: N 字样）；解析不出不写该字段（unrestricted）
    for d in docs:
        act = _extract_act(d["content"])
        if act is not None:
            d["metadata"]["act"] = act

    return docs


def _row_to_text(table: str, cols: List[str], row: tuple) -> str:
    """将一行结构化数据转为自然语言文本。"""
    data = dict(zip(cols, row))
    name = data.get("name") or data.get("slug", "")

    lines = [name]

    if table == "bosses":
        parts = []
        if data.get("hp"): parts.append(f"HP: {data['hp']}")
        if data.get("damage"): parts.append(f"伤害: {data['damage']}")
        if data.get("defense"): parts.append(f"防御: {data['defense']}")
        if data.get("knockback_resist"): parts.append(f"击退抗性: {data['knockback_resist']}")
        if data.get("environment"): parts.append(f"环境: {data['environment']}")
        if data.get("location"): parts.append(f"位置: {data['location']}")
        if data.get("area_name"): parts.append(f"区域: {data['area_name']}")
        if data.get("hp_base"): parts.append(f"HP: {data['hp_base']}")
        if data.get("geo"): parts.append(f"Geo: {data['geo']}")
        if data.get("optional"): parts.append("可选 Boss" if data['optional'] else "主线 Boss")
        if data.get("description"): lines.append(data["description"])
        if parts:
            lines.append(" | ".join(parts))

    elif table == "enemies":
        parts = []
        if data.get("hp"): parts.append(f"HP: {data['hp']}")
        if data.get("damage"): parts.append(f"伤害: {data['damage']}")
        if data.get("defense"): parts.append(f"防御: {data['defense']}")
        if data.get("geo_drop"): parts.append(f"Geo掉落: {data['geo_drop']}")
        if data.get("location"): parts.append(f"位置: {data['location']}")
        if data.get("environment"): parts.append(f"环境: {data['environment']}")
        if data.get("coins"): parts.append(f"金币: {data['coins']}")
        if data.get("description"): lines.append(data["description"])
        if parts:
            lines.append(" | ".join(parts))

    elif table == "charms":
        parts = []
        if data.get("notch_cost"): parts.append(f"槽位: {data['notch_cost']}")
        if data.get("cost"): parts.append(f"价格: {data['cost']}")
        if data.get("effect"): lines.append(f"效果: {data['effect']}")
        if data.get("description"): lines.append(data["description"])
        if data.get("location"): parts.append(f"获取: {data['location']}")
        if data.get("acquisition"): lines.append(f"获取方式: {data['acquisition']}")
        if parts:
            lines.append(" | ".join(parts))

    elif table == "skills":
        if data.get("kind"): lines.append(f"类型: {data['kind']}")
        if data.get("effect"): lines.append(f"效果: {data['effect']}")
        if data.get("description"): lines.append(data["description"])
        if data.get("damage"): lines.append(f"伤害: {data['damage']}")
        if data.get("soul_cost"): lines.append(f"魂耗: {data['soul_cost']}")
        if data.get("acquisition"): lines.append(f"获取: {data['acquisition']}")
        if data.get("area_name"): lines.append(f"区域: {data['area_name']}")

    elif table == "areas":
        if data.get("description"): lines.append(data["description"])
        if data.get("connects_to"): lines.append(f"连接区域: {data['connects_to']}")
        if data.get("music"): lines.append(f"BGM: {data['music']}")

    elif table == "characters":
        if data.get("role"): lines.append(f"身份: {data['role']}")
        if data.get("description"): lines.append(data["description"])
        parts = []
        if data.get("location"): parts.append(f"位置: {data['location']}")
        if data.get("hp"): parts.append(f"HP: {data['hp']}")
        if data.get("damage"): parts.append(f"伤害: {data['damage']}")
        if parts:
            lines.append(" | ".join(parts))

    elif table == "items":
        if data.get("kind"): lines.append(f"类型: {data['kind']}")
        if data.get("effect"): lines.append(f"效果: {data['effect']}")
        if data.get("description"): lines.append(data["description"])
        parts = []
        if data.get("location"): parts.append(f"位置: {data['location']}")
        if data.get("cost"): parts.append(f"价格: {data['cost']}")
        if parts:
            lines.append(" | ".join(parts))

    else:
        # 通用 fallback
        for col in cols:
            if col not in ("slug", "wiki_slug", "verified"):
                val = data.get(col)
                if val is not None and val != "":
                    lines.append(f"{col}: {val}")

    return "\n".join(lines)


def load_structured_data(game_key: str) -> List[Dict]:
    """从游戏的 .db 文件加载结构化数据，转为自然语言文档。"""
    import sqlite3

    db_path = Path(GAME_DB_MAPPING[game_key])
    if not db_path.exists():
        print(f"  ℹ️ 无结构化数据库: {db_path.name}")
        return []

    docs = []
    used_tables = []
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [t[0] for t in cur.fetchall()]

    for table in tables:
        # game_meta：库元信息；sqlite_sequence：自增计数；pages：整页原文，
        # 与 wiki_data.md 完全重复（BG3 实测 9342 页标题全部能在 wiki 里找到），
        # 不跳过会让块数几乎翻倍、造成重复命中
        if table in ("game_meta", "sqlite_sequence", "pages"):
            continue
        used_tables.append(table)
        cur.execute(f"SELECT * FROM \"{table}\"")
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()

        for row in rows:
            text = _row_to_text(table, cols, row)
            name = row["name"] if "name" in cols else (row["slug"] if "slug" in cols else table)
            meta = {"title": name, "category": f"struct/{table}"}
            # 防剧透 P1：locations/quests 等表带 act 列（BG3）时透传进元数据
            if "act" in cols and row["act"]:
                try:
                    meta["act"] = int(row["act"])
                except (TypeError, ValueError):
                    pass
            docs.append({
                "content": text,
                "metadata": meta,
            })

    conn.close()
    print(f"  🗄️  {len(docs)} 条结构化数据 ({', '.join(used_tables)})")
    return docs


def collect_documents(game_key: str) -> List[Dict]:
    """汇总建库文档：英文 wiki + 中文 wiki（若有）+ 结构化数据库。

    保证每篇文档都带 language 元数据（见文件头「元数据契约」）。
    """
    cfg = GAME_DATA[game_key]
    docs = load_wiki_documents(cfg["data_path"], language="en")
    en_chars = sum(len(d["content"]) for d in docs)
    print(f"  📄 英文 Wiki: {len(docs)} 篇, ~{en_chars:,} 字符")

    zh_path = cfg.get("data_path_zh") or ""
    if zh_path and Path(zh_path).exists():
        zh_docs = load_wiki_documents(zh_path, language="zh")
        print(f"  📄 中文 Wiki: {len(zh_docs)} 篇")
        docs.extend(zh_docs)
    else:
        print("  📄 中文 Wiki: 无")

    struct_docs = load_structured_data(game_key)
    for d in struct_docs:
        d["metadata"]["language"] = "en"
    docs.extend(struct_docs)

    missing = [d for d in docs if not d["metadata"].get("language")]
    if missing:
        raise RuntimeError(
            f"{len(missing)} 篇文档缺少 language 元数据，检索会被 filter 过滤掉"
        )
    return docs


def build_vectorstore(game_key: str):
    import os
    cfg = GAME_DATA[game_key]
    print(f"\n{'='*50}")
    print(f"📦 {cfg['name']}")
    print(f"{'='*50}")

    # 1. 汇总 wiki（中英）+ 结构化数据，全部带 language 元数据
    docs = collect_documents(game_key)
    if not docs:
        print("  ❌ 没有文档可处理")
        return
    total_chars = sum(len(d["content"]) for d in docs)
    print(f"  📚 合计: {len(docs)} 文档, ~{total_chars:,} 字符")

    # 2. 导入
    from langchain_text_splitters import RecursiveCharacterTextSplitter
    from langchain_community.embeddings.fastembed import FastEmbedEmbeddings
    from langchain_community.vectorstores import FAISS
    from langchain_core.documents import Document

    # 3. Embedding 模型
    # 默认使用配置中的多语言模型（支持中文检索英文文档）
    try:
        from rag_agent.config import FASTEMBED_MODEL as DEFAULT_MODEL
    except ImportError:
        DEFAULT_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    model_name = os.environ.get("FASTEMBED_MODEL", DEFAULT_MODEL)
    print(f"  🧠 {model_name}")
    embed_model = FastEmbedEmbeddings(model_name=model_name)

    # 4. 分块
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=800,
        chunk_overlap=160,
        separators=["\n## ", "\n# ", "\n\n", "\n", ". ", " ", ""],
    )
    documents = [
        Document(page_content=d["content"], metadata=d["metadata"]) for d in docs
    ]
    chunks = splitter.split_documents(documents)
    # 标题/实体名前置进 chunk，避免长块稀释实体信号（见 vectorstore.prepend_title_to_chunks）
    from rag_agent.vectorstore import prepend_title_to_chunks
    chunks = prepend_title_to_chunks(chunks)
    print(f"  🧩 {len(chunks)} 个分块")

    if not chunks:
        print("  ❌ 无分块结果")
        return

    # 5. 构建 FAISS
    out_dir = Path(cfg["vectorstore_dir"])
    print(f"  🔨 构建 FAISS 索引...")
    vectorstore = FAISS.from_documents(chunks, embed_model)

    # 6. 保存
    out_dir.mkdir(parents=True, exist_ok=True)
    vectorstore.save_local(str(out_dir))
    print(f"  ✅ 已保存: {out_dir} ({vectorstore.index.ntotal} 向量)")

    # 7. 验证
    vs2 = FAISS.load_local(str(out_dir), embed_model, allow_dangerous_deserialization=True)
    print(f"  ✅ 验证通过: {vs2.index.ntotal} 向量")


def main():
    parser = argparse.ArgumentParser(description="Mac 本地向量库构建")
    parser.add_argument("--game", "-g", required=True,
                        choices=list(GAME_DATA.keys()) + ["all"],
                        help="要构建的游戏")
    args = parser.parse_args()

    if args.game == "all":
        for key in GAME_DATA:
            build_vectorstore(key)
    else:
        build_vectorstore(args.game)

    print(f"\n🎉 全部完成！")
    print("请将向量库传回服务器：")
    targets = list(GAME_DATA.keys()) if args.game == "all" else [args.game]
    for key in targets:
        print(f"  bash scripts/deploy/vectorstores.sh {key}")
    print()
    print("覆盖后重启服务器 API 即可生效：bash scripts/deploy/api.sh")


if __name__ == "__main__":
    main()
