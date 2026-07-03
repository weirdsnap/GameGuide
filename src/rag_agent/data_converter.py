"""空洞骑士数据转文档：将 HallownestAPI 的 JSON 数据转为自然语言文本块。"""

import json
from pathlib import Path
from typing import Dict, Any, List


def load_json_file(path: Path) -> Dict[str, Any]:
    """安全加载 JSON 文件。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"  ⚠️ 跳过 {path.name}: {e}")
        return {}


def area_to_text(data: Dict[str, Any]) -> str:
    """区域数据转文本。"""
    lines = [f"# 区域：{data.get('name', '未知')}"]
    if data.get("summary"):
        lines.append(data["summary"])
    if data.get("description"):
        lines.append(data["description"])

    # 连接关系（支持新 dict 格式含能力锁，和旧 list 格式）
    conns = data.get("connectsTo")
    if isinstance(conns, dict):
        conn_lines = []
        for target, reqs in conns.items():
            if reqs:
                conn_lines.append(f"→ {target}（需要：{'、'.join(reqs)}）")
            else:
                conn_lines.append(f"→ {target}")
        lines.append("连接区域：\n" + "\n".join(conn_lines))
    elif isinstance(conns, list):
        lines.append(f"连接区域：{'、'.join(conns)}")

    if data.get("stagStation"):
        lines.append(f"鹿角站：{data['stagStation']}")
    music = data.get("music")
    if isinstance(music, dict):
        if music.get("title"):
            lines.append(f"背景音乐：{music['title']}")
    elif isinstance(music, str):
        lines.append(f"背景音乐：{music}")
    return "\n\n".join(lines)


def boss_to_text(data: Dict[str, Any]) -> str:
    """Boss 数据转文本。"""
    lines = [f"# Boss：{data.get('name', '未知')}"]
    if data.get("health"):
        lines.append(f"生命值：{data['health']}")
    if data.get("location"):
        lines.append(f"位置：{data['location']}")
    if data.get("description"):
        lines.append(data["description"])
    if data.get("phases"):
        phases = []
        for p in data["phases"]:
            phase_text = p.get("name", "")
            if p.get("description"):
                phase_text += f"：{p['description']}"
            if p.get("attacks"):
                phase_text += f"（攻击方式：{'、'.join(p['attacks'])}）"
            phases.append(phase_text)
        lines.append("阶段：\n- " + "\n- ".join(phases))
    if data.get("rewards"):
        lines.append(f"奖励：{data['rewards']}")
    if data.get("dreamNailed"):
        lines.append(f"梦之钉对话：{data['dreamNailed']}")
    return "\n\n".join(lines)


def character_to_text(data: Dict[str, Any]) -> str:
    """角色/NPC 数据转文本。"""
    lines = [f"# 角色：{data.get('name', '未知')}"]
    if data.get("kind"):
        lines.append(f"类型：{data['kind']}")
    if data.get("location"):
        lines.append(f"位置：{data['location']}")
    if data.get("summary"):
        lines.append(data["summary"])
    if data.get("description"):
        lines.append(data["description"])
    if data.get("subtext"):
        lines.append(data["subtext"])
    if data.get("dialogue"):
        lines.append(f"对话：{data['dialogue']}")
    if data.get("significance"):
        lines.append(f"重要性：{data['significance']}")
    return "\n\n".join(lines)


def charm_to_text(data: Dict[str, Any]) -> str:
    """护符数据转文本。"""
    lines = [f"# 护符：{data.get('name', '未知')}"]
    if data.get("description"):
        lines.append(data["description"])
    if data.get("effect"):
        lines.append(f"效果：{data['effect']}")
    if data.get("notchCost"):
        lines.append(f"槽位消耗：{data['notchCost']}")
    if data.get("location"):
        lines.append(f"获取位置：{data['location']}")
    if data.get("kind"):
        lines.append(f"类型：{data['kind']}")
    if data.get("boss"):
        lines.append(f"关联Boss：{data['boss']}")
    if data.get("requires"):
        lines.append(f"前置条件：{data['requires']}")
    if data.get("significance"):
        lines.append(f"重要性：{data['significance']}")
    if data.get("synergies"):
        synergies = data["synergies"]
        for sync in synergies:
            if isinstance(sync, dict):
                sync_name = sync.get("name", sync.get("charm", ""))
                sync_effect = sync.get("effect", "")
                lines.append(f"联动（{sync_name}）：{sync_effect}")
            elif isinstance(sync, str):
                lines.append(f"联动：{sync}（护符名称）")
    return "\n\n".join(lines)


def skill_to_text(data: Dict[str, Any]) -> str:
    """技能数据转文本。"""
    lines = [f"# 技能/法术：{data.get('name', '未知')}"]
    if data.get("kind"):
        lines.append(f"类型：{data['kind']}")
    if data.get("description"):
        lines.append(data["description"])
    if data.get("effect"):
        lines.append(f"效果：{data['effect']}")
    if data.get("acquisition"):
        lines.append(f"获取方式：{data['acquisition']}")
    if data.get("area"):
        lines.append(f"所在区域：{data['area']}")
    if data.get("cost"):
        lines.append(f"消耗：{data['cost']}")
    if data.get("uses"):
        lines.append(f"用途：{data['uses']}")
    if data.get("lore"):
        lines.append(f"背景故事：{data['lore']}")
    if data.get("dialogue"):
        lines.append(f"对话：{data['dialogue']}")
    return "\n\n".join(lines)


def json_to_documents(data_dir: str) -> List[Dict[str, Any]]:
    """
    读取 data 目录下的所有 JSON 文件，转为文档列表。

    返回格式：[{"text": str, "metadata": {"source": ..., "category": ..., "name": ..., "slug": ...}}, ...]
    这样不依赖 langchain，任何环境都能处理。
    """
    data_root = Path(data_dir)
    if not data_root.exists():
        raise FileNotFoundError(f"数据目录不存在：{data_dir}")

    converters = {
        "areas": area_to_text,
        "bosses": boss_to_text,
        "characters": character_to_text,
        "charms": charm_to_text,
        "skills": skill_to_text,
    }

    documents = []
    for category, converter in converters.items():
        category_dir = data_root / category
        if not category_dir.exists():
            continue

        json_files = sorted(category_dir.glob("*.json"))
        for file_path in json_files:
            # 跳过 _all.json 聚合文件（只处理单个条目文件）
            if file_path.stem.startswith("_"):
                continue
            data = load_json_file(file_path)
            if not data:
                continue
            text = converter(data)
            documents.append({
                "text": text,
                "metadata": {
                    "source": f"{category}/{file_path.name}",
                    "category": category,
                    "name": data.get("name", file_path.stem),
                    "slug": data.get("slug", file_path.stem),
                },
            })

    return documents


def export_as_markdown(documents: List[Dict[str, Any]], output_path: str):
    """将所有文档导出为单文件 Markdown（方便跨平台处理）。"""
    lines = []
    for i, doc in enumerate(documents):
        meta = doc["metadata"]
        lines.append(f"---\n# 文档 [{i+1}] {meta['name']}\n")
        lines.append(f"- 类别：{meta['category']}")
        lines.append(f"- 标识：{meta['slug']}")
        lines.append(f"- 路径：{meta['source']}\n")
        lines.append(doc["text"])
        lines.append("\n")
    Path(output_path).write_text("\n".join(lines), encoding="utf-8")
    print(f"已导出 {len(documents)} 个文档到：{output_path}")
