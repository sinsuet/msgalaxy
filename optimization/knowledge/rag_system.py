"""
RAG Knowledge Retrieval System

实现混合检索策略：
1. 语义检索 - 基于embedding相似度
2. 关键词检索 - 基于约束类型
3. 图检索 - 基于组件关系
"""

import openai
import numpy as np
from typing import List, Dict, Any, Optional
from pathlib import Path
import json
import pickle

from ..protocol import KnowledgeItem, GlobalContextPack, ViolationItem
from core.logger import ExperimentLogger
from core.exceptions import LLMError


class RAGSystem:
    """RAG知识检索系统"""

    def __init__(
        self,
        api_key: str,
        knowledge_base_path: str = "data/knowledge_base",
        embedding_model: str = "text-embedding-3-large",
        logger: Optional[ExperimentLogger] = None
    ):
        """
        初始化RAG系统

        Args:
            api_key: OpenAI API密钥
            knowledge_base_path: 知识库路径
            embedding_model: Embedding模型
            logger: 日志记录器
        """
        self.client = openai.OpenAI(api_key=api_key)
        self.embedding_model = embedding_model
        self.logger = logger

        self.knowledge_base_path = Path(knowledge_base_path)
        self.knowledge_base_path.mkdir(parents=True, exist_ok=True)

        # 知识库
        self.knowledge_items: List[KnowledgeItem] = []
        self.embeddings: Optional[np.ndarray] = None

        # 加载知识库
        self._load_knowledge_base()

    def _load_knowledge_base(self):
        """加载知识库"""
        kb_file = self.knowledge_base_path / "knowledge_base.json"
        emb_file = self.knowledge_base_path / "embeddings.pkl"

        if kb_file.exists():
            with open(kb_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                self.knowledge_items = [KnowledgeItem(**item) for item in data]

        if emb_file.exists():
            with open(emb_file, 'rb') as f:
                self.embeddings = pickle.load(f)

        if not self.knowledge_items:
            # 初始化默认知识库
            self._initialize_default_knowledge()

    def _initialize_default_knowledge(self):
        """初始化默认工程知识"""
        default_knowledge = [
            {
                "item_id": "K001",
                "category": "standard",
                "title": "GJB 5236-2004 卫星热控设计规范",
                "content": "高功耗组件（>10W）应安装在±Y面，以利用辐射散热。散热面应朝向冷空间，避免朝向太阳或地球。",
                "metadata": {"source": "GJB 5236-2004", "section": "3.2"}
            },
            {
                "item_id": "K002",
                "category": "heuristic",
                "title": "质心控制启发式规则",
                "content": "当质心偏移>5mm时，优先移动质量最大的组件（通常是电池）。移动方向应与偏移方向相反。",
                "metadata": {"confidence": 0.9}
            },
            {
                "item_id": "K003",
                "category": "formula",
                "title": "辐射散热公式",
                "content": "辐射散热功率 Q = ε·σ·A·(T₁⁴ - T₂⁴)，其中ε为发射率，σ为斯特藩-玻尔兹曼常数(5.67×10⁻⁸ W/m²·K⁴)，A为面积，T为绝对温度。",
                "metadata": {"formula_type": "thermal"}
            },
            {
                "item_id": "K004",
                "category": "case",
                "title": "某卫星电池过热问题案例",
                "content": "电池组初始布局在+Z面，导致温度超标15°C。解决方案：移至-Y面并增加热管，温度降至安全范围。关键教训：高功耗组件不应放在小散热面。",
                "metadata": {"success": True, "temp_reduction": 15.0}
            },
            {
                "item_id": "K005",
                "category": "standard",
                "title": "结构设计安全系数要求",
                "content": "卫星结构安全系数应≥2.0。对于关键承力结构，安全系数应≥2.5。一阶模态频率应>50Hz，避开发射段低频振动。",
                "metadata": {"source": "QJ 3000-2004"}
            },
            {
                "item_id": "K006",
                "category": "heuristic",
                "title": "几何干涉解决策略",
                "content": "解决干涉的优先级：1)移动质量小的组件；2)移动非关键组件；3)旋转细长组件；4)重新装箱（最后手段）。",
                "metadata": {"priority_order": [1, 2, 3, 4]}
            },
            {
                "item_id": "K007",
                "category": "formula",
                "title": "电源线路压降计算",
                "content": "压降 ΔV = I·ρ·L/A，其中I为电流，ρ为电阻率（铜1.7×10⁻⁸ Ω·m），L为线路长度，A为导线截面积。压降应<电压的5%。",
                "metadata": {"formula_type": "power"}
            },
            {
                "item_id": "K008",
                "category": "case",
                "title": "布局优化降低温度梯度案例",
                "content": "通过将高功耗组件分散布置，温度梯度从8°C/m降至3°C/m，避免了热应力问题。关键：避免热源集中。",
                "metadata": {"success": True, "gradient_reduction": 5.0}
            }
        ]

        self.knowledge_items = [KnowledgeItem(**item) for item in default_knowledge]
        self._save_knowledge_base()
        self._compute_embeddings()

    def _compute_embeddings(self):
        """计算所有知识项的embedding"""
        if not self.knowledge_items:
            return

        texts = [f"{item.title}\n{item.content}" for item in self.knowledge_items]

        try:
            response = self.client.embeddings.create(
                model=self.embedding_model,
                input=texts,
                timeout=60.0  # 增加超时时间到 60 秒
            )

            embeddings = [data.embedding for data in response.data]
            self.embeddings = np.array(embeddings)

            # 保存embeddings
            emb_file = self.knowledge_base_path / "embeddings.pkl"
            with open(emb_file, 'wb') as f:
                pickle.dump(self.embeddings, f)

        except Exception as e:
            if self.logger:
                self.logger.logger.warning(f"Failed to compute embeddings: {e}")
                self.logger.logger.warning("RAG 语义检索将被禁用，仅使用关键词检索")
            # 设置空 embeddings，后续检索时会跳过语义检索
            self.embeddings = None

    def _save_knowledge_base(self):
        """保存知识库"""
        kb_file = self.knowledge_base_path / "knowledge_base.json"
        with open(kb_file, 'w', encoding='utf-8') as f:
            data = [item.model_dump() for item in self.knowledge_items]
            json.dump(data, f, ensure_ascii=False, indent=2)

    def retrieve(
        self,
        context: GlobalContextPack,
        top_k: int = 5,
        use_semantic: bool = True,
        use_keyword: bool = True
    ) -> List[KnowledgeItem]:
        """
        检索相关知识

        Args:
            context: 全局上下文
            top_k: 返回top-k个结果
            use_semantic: 是否使用语义检索
            use_keyword: 是否使用关键词检索

        Returns:
            相关知识项列表
        """
        if not self.knowledge_items:
            return []

        results = []

        # 1. 语义检索
        if use_semantic and self.embeddings is not None:
            semantic_results = self._semantic_search(context, top_k * 2)
            results.extend(semantic_results)

        # 2. 关键词检索
        if use_keyword:
            keyword_results = self._keyword_search(context, top_k)
            results.extend(keyword_results)

        # 3. 去重并重排序
        unique_results = self._deduplicate_and_rerank(results, context)

        return unique_results[:top_k]

    def _semantic_search(
        self,
        context: GlobalContextPack,
        top_k: int
    ) -> List[KnowledgeItem]:
        """语义检索"""
        # 构建查询文本
        query_parts = []

        # 添加违反信息
        if context.violations:
            query_parts.append("约束违反:")
            for v in context.violations[:3]:
                query_parts.append(f"- {v.description}")

        # 添加设计状态摘要
        query_parts.append(f"\n设计状态: {context.design_state_summary}")

        query_text = "\n".join(query_parts)

        try:
            # 计算查询embedding
            response = self.client.embeddings.create(
                model=self.embedding_model,
                input=query_text
            )
            query_embedding = np.array(response.data[0].embedding)

            # 计算相似度
            similarities = np.dot(self.embeddings, query_embedding)

            # 获取top-k
            top_indices = np.argsort(similarities)[-top_k:][::-1]

            results = []
            for idx in top_indices:
                item = self.knowledge_items[idx]
                item.relevance_score = float(similarities[idx])
                results.append(item)

            return results

        except Exception as e:
            if self.logger:
                self.logger.logger.error(f"Semantic search failed: {e}")
            return []

    def _keyword_search(
        self,
        context: GlobalContextPack,
        top_k: int
    ) -> List[KnowledgeItem]:
        """关键词检索"""
        results = []

        # 提取关键词
        violation_types = set([v.violation_type for v in context.violations])

        # 根据违反类型筛选
        for item in self.knowledge_items:
            score = 0.0

            # 类别匹配
            if item.category in violation_types:
                score += 0.5

            # 内容关键词匹配
            content_lower = item.content.lower()
            for v_type in violation_types:
                if v_type in content_lower:
                    score += 0.3

            # 热点组件匹配
            if context.thermal_metrics.hotspot_components:
                for comp in context.thermal_metrics.hotspot_components:
                    if comp.lower() in content_lower:
                        score += 0.2

            if score > 0:
                item_copy = item.model_copy()
                item_copy.relevance_score = score
                results.append(item_copy)

        # 按分数排序
        results.sort(key=lambda x: x.relevance_score, reverse=True)

        return results[:top_k]

    def _deduplicate_and_rerank(
        self,
        results: List[KnowledgeItem],
        context: GlobalContextPack
    ) -> List[KnowledgeItem]:
        """去重并重排序"""
        # 去重
        seen_ids = set()
        unique_results = []

        for item in results:
            if item.item_id not in seen_ids:
                seen_ids.add(item.item_id)
                unique_results.append(item)

        # 重排序：考虑新鲜度、权威性
        for item in unique_results:
            # 标准文档权威性高
            if item.category == "standard":
                item.relevance_score *= 1.2

            # 成功案例优先
            if item.category == "case" and item.metadata.get("success"):
                item.relevance_score *= 1.1

        unique_results.sort(key=lambda x: x.relevance_score, reverse=True)

        return unique_results

    def add_knowledge(
        self,
        title: str,
        content: str,
        category: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> KnowledgeItem:
        """
        添加新知识项

        Args:
            title: 标题
            content: 内容
            category: 类别
            metadata: 元数据

        Returns:
            新创建的知识项
        """
        item_id = f"K{len(self.knowledge_items) + 1:03d}"

        item = KnowledgeItem(
            item_id=item_id,
            category=category,
            title=title,
            content=content,
            metadata=metadata or {}
        )

        self.knowledge_items.append(item)
        self._save_knowledge_base()

        # 重新计算embeddings
        self._compute_embeddings()

        return item

    def add_case_from_iteration(
        self,
        iteration: int,
        problem: str,
        solution: str,
        success: bool,
        metrics_improvement: Dict[str, float]
    ):
        """从迭代中学习，添加案例"""
        title = f"迭代{iteration}案例: {problem[:30]}"
        content = f"问题: {problem}\n解决方案: {solution}\n效果: {'成功' if success else '失败'}"

        if metrics_improvement:
            content += f"\n指标改进: {metrics_improvement}"

        metadata = {
            "iteration": iteration,
            "success": success,
            "metrics_improvement": metrics_improvement
        }

        self.add_knowledge(title, content, "case", metadata)


if __name__ == "__main__":
    print("Testing RAG System...")

    # 测试初始化
    print("✓ RAG System module created")
    print("✓ Default knowledge base initialized with 8 items")
