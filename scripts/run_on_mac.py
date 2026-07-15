#!/usr/bin/env python3
"""
🖥️ Mac 本地运行脚本 — 构建所有游戏的向量库（FAISS）

服务器 CPU/RAM 不足（3.6GB），无法跑 fastembed + FAISS。
请在你的 MacBook 上运行此脚本，然后将生成的 vectorstore 目录传回服务器。

用法：
    cd /data/learning/agent
    python3 scripts/run_on_mac.py --game all          ← 构建所有游戏
     python3 scripts/run_on_mac.py --game oni           ← 只构建某个游戏
     python3 scripts/run_on_mac.py --game terraria
     python3 scripts/run_on_mac.py --game silksong
     python3 scripts/run_on_mac.py --game hollow_knight
     python3 scripts/run_on_mac.py --game cyberpunk2077
     python3 scripts/run_on_mac.py --game va11halla

前置条件：
    pip install -r requirements.txt              # 安装 fastembed 等依赖
    # 如果在大陆，可能需要设置 huggingface 镜像：
    # export HF_ENDPOINT=https://hf-mirror.com

输出目录：
    games/{game}/vectorstore/                     ← 每个游戏独立的向量库
"""

import argparse
import re
import sys
from pathlib import Path
from typing import List, Dict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
GAMES_DIR = PROJECT_ROOT / "games"

GAME_DATA: Dict[str, Dict[str, str]] = {
    "hollow_knight": {
        "name": "Hollow Knight (空洞骑士)",
        "data_path": str(PROJECT_ROOT / "data" / "wiki_data.md"),
        "vectorstore_dir": str(PROJECT_ROOT / "vectorstore"),
    },
    "oni": {
        "name": "Oxygen Not Included (缺氧)",
        "data_path": str(GAMES_DIR / "oni" / "data" / "wiki_data.md"),
        "vectorstore_dir": str(GAMES_DIR / "oni" / "vectorstore"),
    },
    "terraria": {
        "name": "Terraria (泰拉瑞亚)",
        "data_path": str(GAMES_DIR / "terraria" / "data" / "wiki_data.md"),
        "vectorstore_dir": str(GAMES_DIR / "terraria" / "vectorstore"),
    },
    "silksong": {
        "name": "Hollow Knight: Silksong (丝之歌)",
        "data_path": str(GAMES_DIR / "silksong" / "data" / "wiki_data.md"),
        "vectorstore_dir": str(GAMES_DIR / "silksong" / "vectorstore"),
    },
    "cyberpunk2077": {
        "name": "Cyberpunk 2077 (赛博朋克2077)",
        "data_path": str(GAMES_DIR / "cyberpunk2077" / "data" / "wiki_data.md"),
        "vectorstore_dir": str(GAMES_DIR / "cyberpunk2077" / "vectorstore"),
    },
    "va11halla": {
        "name": "VA-11 Hall-A (赛博朋克酒保行动)",
        "data_path": str(GAMES_DIR / "va11halla" / "data" / "wiki_data.md"),
        "vectorstore_dir": str(GAMES_DIR / "va11halla" / "vectorstore"),
    },
}


def load_wiki_documents(filepath: str) -> List[Dict]:
    """从 wiki_data.md 加载文档列表。"""
    path = Path(filepath)
    if not path.exists():
        print(f"  ❌ 找不到数据文件：{filepath}")
        return []

    text = path.read_text(encoding="utf-8")

    # 以 # 文档： 或 ## 标题 作为文档分割标记
    # 支持两种格式：
    #   格式 A: # 文档：标题   ← 旧格式
    #   格式 B: ## 标题        ← fetch_wiki.py 格式
    split_a = re.split(r"(?=^# 文档[：:])", text, flags=re.MULTILINE)
    split_b = re.split(r"(?=^##\s+.*(?:\n|$))", text, flags=re.MULTILINE)

    # 判断使用哪种格式：优先用格式 A
    chunks_a = [c for c in split_a if c.strip().startswith("# 文档")]
    chunks_b = [c for c in split_b if c.strip().startswith("##") and not c.strip().startswith("###") and not c.strip().startswith("####")]
    # 跳过第一个分块如果它是文件标题（没有 ##）
    if split_b and not split_b[0].strip().startswith("##"):
        split_b = split_b[1:]
        chunks_b = [c for c in split_b if c.strip().startswith("##") and not c.strip().startswith("###") and not c.strip().startswith("####")]

    if chunks_a:
        chunks = split_a
        fmt = "doc"
    elif chunks_b:
        chunks = split_b
        fmt = "h2"
    else:
        print("  ℹ️ 无法识别文档格式")
        return []

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
        else:
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

        if content:
            docs.append({
                "content": content,
                "metadata": {"title": title, "category": category},
            })

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
        if data.get("description"): None  # already handled
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

    db_mapping = {
        "hollow_knight": "games/hollow_knight/hk_data.db",
        "oni": "games/oni/oni_data.db",
        "terraria": "games/terraria/terraria_data.db",
        "silksong": "games/silksong/silksong_data.db",
        "cyberpunk2077": "games/cyberpunk2077/cyberpunk2077_data.db",
        "va11halla": "games/va11halla/va11halla_data.db",
    }

    db_path = Path(__file__).resolve().parent.parent / db_mapping[game_key]
    if not db_path.exists():
        print(f"  ℹ️ 无结构化数据库: {db_path.name}")
        return []

    docs = []
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [t[0] for t in cur.fetchall()]

    for table in tables:
        if table == "game_meta":
            continue
        cur.execute(f"SELECT * FROM \"{table}\"")
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()

        for row in rows:
            text = _row_to_text(table, cols, row)
            name = row["name"] if "name" in cols else (row["slug"] if "slug" in cols else table)
            docs.append({
                "content": text,
                "metadata": {"title": name, "category": f"struct/{table}"},
            })

    conn.close()
    print(f"  🗄️  {len(docs)} 条结构化数据 ({', '.join(tables)})")
    return docs


def build_vectorstore(game_key: str):
    import os
    cfg = GAME_DATA[game_key]
    print(f"\n{'='*50}")
    print(f"📦 {cfg['name']}")
    print(f"{'='*50}")

    # 1. 加载 Wiki 文档
    wiki_docs = load_wiki_documents(cfg["data_path"])
    wiki_chars = sum(len(d["content"]) for d in wiki_docs)
    print(f"  📄 {len(wiki_docs)} 篇 Wiki 文档, ~{wiki_chars:,} 字符")

    # 2. 加载结构化数据
    struct_docs = load_structured_data(game_key)

    # 合并
    docs = wiki_docs + struct_docs
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
    model_name = os.environ.get("FASTEMBED_MODEL", "BAAI/bge-small-en-v1.5")
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
    print("请将 vectorstore 目录传回服务器：")
    print("  scp -r games/oni/vectorstore/ snap@114.132.189.56:/data/learning/agent/games/oni/")
    print("  scp -r games/terraria/vectorstore/ snap@114.132.189.56:/data/learning/agent/games/terraria/")
    print("  scp -r games/silksong/vectorstore/ snap@114.132.189.56:/data/learning/agent/games/silksong/")
    print("  scp -r games/cyberpunk2077/vectorstore/ snap@114.132.189.56:/data/learning/agent/games/cyberpunk2077/")
    print("  scp -r games/va11halla/vectorstore/ snap@114.132.189.56:/data/learning/agent/games/va11halla/")
    print("  scp -r vectorstore/ snap@114.132.189.56:/data/learning/agent/vectorstore/   (如果重建 HK)")
    print()
    print("覆盖后重启服务器即可生效。")


if __name__ == "__main__":
    main()
