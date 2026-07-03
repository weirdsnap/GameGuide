# 空洞骑士 RAG Agent — 项目状态

## ✅ 已完成

### P0 — 核心 MVP

- [x] HallownestAPI 数据导入与转换（53区域、137连接、12能力锁标注）
- [x] 253条自然语言知识文档（hallownest_knowledge.md）
- [x] 本地 embedding 生成（Mac M3 → BAAI/bge-small-en-v1.5）
- [x] FAISS 向量库构建 & 上传服务器（255 vectors, 384 dims）
- [x] 服务器端 fastembed 模型下载（hf-mirror 镜像）
- [x] LangChain 1.3.11 新 API（create_agent）适配
- [x] DeepSeek V4 + RAG 查询链路打通
- [x] 3 个测试问题验证通过
- [x] GitHub 代码推送

### P1 — 体验完善

- [x] 向量库加载缓存（避免每次工具调用重复加载）

## 📋 待办

### P1 — 体验完善

- [ ] Web UI / 飞书机器人集成
- [ ] 交互式 CLI 测试更多场景（Boss 攻略、护符搭配等）

### P2 — 进阶功能

- [ ] 多轮对话上下文记忆
- [ ] 数据更新脚本（当 HallownestAPI 有变化时重建）
- [ ] 中文模型（BAAI/bge-large-zh-v1.5 等）可选方案
- [ ] 查询结果排序优化（MMR / 重新排序）

## 🐛 已知问题

- `get_retriever()` 每次工具调用都会重新加载向量库（当前通过模块级缓存缓解）

## 📊 技术栈

| 组件 | 方案 |
|------|------|
| 向量库 | FAISS (langchain_community) |
| 本地 Embedding | fastembed + BAAI/bge-small-en-v1.5 |
| LLM | DeepSeek V4 Flash (OpenAI 兼容) |
| Agent 框架 | LangChain 1.3.11 + LangGraph |
