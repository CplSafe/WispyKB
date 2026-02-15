# 智能分块服务
# 基于 MaxKB 的分块策略实现
# 支持：递归分块、父链保留、关键词提取、智能断句

import re
import logging
from typing import List, Dict, Optional, Callable
from functools import reduce

logger = logging.getLogger(__name__)

try:
    import jieba
    JIEBA_AVAILABLE = True
except ImportError:
    JIEBA_AVAILABLE = False
    logger.warning("jieba 未安装，关键词提取功能不可用: pip install jieba")


class SmartChunker:
    """
    智能分块器

    功能：
    1. 递归分块 - 按标题层级 (H1-H6) 树形解析
    2. 父链保留 - 每个 chunk 保留父级标题上下文
    3. 智能断句 - 在句号、问号等位置断句
    4. 关键词提取 - 使用 jieba 提取中文关键词
    """

    # 默认 Markdown 标题正则模式
    DEFAULT_PATTERNS = [
        re.compile('(?<=^)# .*|(?<=\\n)# .*'),
        re.compile('(?<=\\n)(?<!#)## (?!#).*|(?<=^)(?<!#)## (?!#).*'),
        re.compile("(?<=\\n)(?<!#)### (?!#).*|(?<=^)(?<!#)### (?!#).*"),
        re.compile("(?<=\\n)(?<!#)#### (?!#).*|(?<=^)(?<!#)#### (?!#).*"),
        re.compile("(?<=\\n)(?<!#)##### (?!#).*|(?<=^)(?<!#)##### (?!#).*"),
        re.compile("(?<=\\n)(?<!#)###### (?!#).*|(?<=^)(?<!#)###### (?!#).*"),
    ]

    def __init__(
        self,
        pattern_list: Optional[List[re.Pattern]] = None,
        with_filter: bool = True,
        limit: int = 100000,
        extract_keywords: bool = True
    ):
        """
        初始化智能分块器

        Args:
            pattern_list: 标题识别正则列表
            with_filter: 是否过滤特殊字符
            limit: 单个分块最大字符数
            extract_keywords: 是否提取关键词
        """
        self.pattern_list = pattern_list or self.DEFAULT_PATTERNS
        self.with_filter = with_filter
        self.limit = max(50, min(limit, 100000))  # 限制在 50-100000
        self.extract_keywords = extract_keywords and JIEBA_AVAILABLE

    def parse(self, text: str, limit: Optional[int] = None) -> List[Dict[str, str]]:
        """
        解析文本并分块

        Args:
            text: 要解析的文本
            limit: 覆盖默认的 limit

        Returns:
            [{"title": "标题链", "content": "内容", "keywords": ["关键词"]}] 列表
        """
        if limit:
            limit = max(50, min(limit, 100000))
        else:
            limit = self.limit

        # 清理文本
        text = self._clean_text(text)

        # 解析为树形结构
        tree = self._parse_to_tree(text, 0)

        # 转换为分块列表
        chunks = self._tree_to_chunks(tree, [], [])

        # 后处理
        chunks = self._post_process(chunks)

        return chunks

    def _clean_text(self, text: str) -> str:
        """清理文本"""
        text = text.replace('\r\n', '\n')
        text = text.replace('\r', '\n')
        text = text.replace("\0", '')
        return text

    def _parse_to_tree(self, text: str, index: int) -> List[Dict]:
        """
        解析文本为树形结构

        Args:
            text: 文本内容
            index: 从第几个正则开始

        Returns:
            树形结构列表
        """
        # 查找当前层级的标题
        level_items = self._parse_level(text, self.pattern_list[index] if index < len(self.pattern_list) else None)

        if not level_items:
            # 没有找到标题，按段落分割
            return [{"content": c, "state": "block"} for c in self._smart_split_paragraph(text, self.limit)]

        # 处理第一个标题前的内容
        cursor = 0
        title_items = [item for item in level_items if item["state"] == "title"]

        for i, item in enumerate(title_items):
            start_content = item["content"]

            # 处理标题前的内容
            title_idx = text.find(start_content, cursor)
            if cursor < title_idx:
                prefix_text = text[cursor:title_idx]
                if prefix_text.strip():
                    # 插入到列表开头
                    for block in self._smart_split_paragraph(prefix_text, self.limit):
                        level_items.insert(0, {"content": block, "state": "block"})
                    title_items = [item for item in level_items if item["state"] == "title"]
                    item = title_items[title_items.index(item)]

            # 获取标题对应的内容
            if i + 1 < len(title_items):
                next_content = title_items[i + 1]["content"]
            else:
                next_content = None

            start_idx = text.find(start_content, cursor)
            end_idx = text.find(next_content, start_idx + 1) if next_content else len(text)

            if start_idx >= 0:
                content_text = text[start_idx + len(start_content):end_idx]

                # 递归解析子内容
                if content_text.strip():
                    children = self._parse_to_tree(content_text, index + 1)
                    item["children"] = children
                else:
                    item["children"] = []

            cursor = end_idx if end_idx > cursor else cursor

        return level_items

    def _parse_level(self, text: str, pattern: Optional[re.Pattern]) -> List[Dict]:
        """
        解析当前层级的标题

        Args:
            text: 文本内容
            pattern: 正则模式

        Returns:
            [{"content": "标题", "state": "title"}] 列表
        """
        if not pattern:
            return [{"content": c, "state": "block"} for c in self._smart_split_paragraph(text, self.limit)]

        matches = []
        for match in pattern.finditer(text):
            content = match.group(0).strip()
            if content:
                matches.append({"content": content[:255], "state": "title"})

        return matches

    def _tree_to_chunks(self, tree: List[Dict], parent_chain: List[str], result: List[Dict]) -> List[Dict]:
        """
        将树形结构转换为分块列表

        Args:
            tree: 树形结构
            parent_chain: 父级标题链
            result: 结果列表

        Returns:
            分块列表
        """
        for item in tree:
            current_chain = parent_chain.copy()

            if item.get("state") == "block":
                content = item.get("content", "")
                if content.strip():
                    title = " > ".join(current_chain)

                    chunk = {
                        "title": title,
                        "content": content if not self.with_filter else self._filter_special_chars(content)
                    }

                    # 提取关键词
                    if self.extract_keywords:
                        chunk["keywords"] = self._extract_keywords(content)

                    result.append(chunk)

            children = item.get("children")
            if children:
                current_chain.append(item.get("content", ""))
                self._tree_to_chunks(children, current_chain, result)

        return result

    def _smart_split_paragraph(self, text: str, limit: int) -> List[str]:
        """
        智能分割段落

        在 limit 前找到合适的分割点（句号、问号、感叹号等）

        Args:
            text: 要分割的文本
            limit: 最大字符数

        Returns:
            分割后的文本列表
        """
        if len(text) <= limit:
            return [text]

        result = []
        start = 0

        while start < len(text):
            end = start + limit

            if end >= len(text):
                result.append(text[start:])
                break

            # 寻找最佳分割点
            best_split = end
            split_chars = [
                ('。', 0), ('.', 0),
                ('！', 0), ('!', 0),
                ('？', 0), ('?', 0),
                ('；', 0), (';', 0),
                ('\n', 0),
            ]

            # 从后往前找
            for i in range(end - 1, start + limit // 2, -1):
                for char, offset in split_chars:
                    if text[i] == char:
                        best_split = i + 1
                        break
                if best_split != end:
                    break

            result.append(text[start:best_split])
            start = best_split

        return [t for t in result if t.strip()]

    def _filter_special_chars(self, text: str) -> str:
        """过滤特殊字符"""
        # 替换多个空格为单个空格
        text = re.sub(r' +', ' ', text)
        # 替换多个换行为单个换行
        text = re.sub(r'\n+', '\n', text)
        # 移除标题标记
        text = re.sub(r'#+', '', text)
        # 移除 tab
        text = re.sub(r"\t+", '', text)
        return text

    def _extract_keywords(self, content: str, top_k: int = 10) -> List[str]:
        """
        提取关键词

        Args:
            content: 文本内容
            top_k: 返回前 k 个关键词

        Returns:
            关键词列表
        """
        if not JIEBA_AVAILABLE:
            return []

        try:
            stopwords = {'：', '"', '！', '"', '\n', '\\s', '的', '了', '是', '在', '和', '与', '或'}
            words = jieba.lcut(content)
            # 过滤停用词和单字
            keywords = [w for w in words if w not in stopwords and len(w) > 1]
            # 统计词频
            word_count = {}
            for w in keywords:
                word_count[w] = word_count.get(w, 0) + 1
            # 按词频排序
            sorted_words = sorted(word_count.items(), key=lambda x: x[1], reverse=True)
            return [w for w, _ in sorted_words[:top_k]]
        except Exception as e:
            logger.warning(f"关键词提取失败: {e}")
            return []

    def _post_process(self, chunks: List[Dict]) -> List[Dict]:
        """后处理分块"""
        result = []

        for chunk in chunks:
            title = chunk.get("title", "")
            content = chunk.get("content", "")

            # 如果内容为空但有标题，使用标题作为内容
            if not content.strip() and title.strip():
                content = title
                title = ""

            # 限制标题长度
            if len(title) > 255:
                content = title[255:] + content
                title = title[:255]

            # 过滤空分块
            if content.strip():
                result.append({
                    "title": title,
                    "content": content,
                    "keywords": chunk.get("keywords", [])
                })

        return result


class SimpleChunker:
    """
    简单分块器

    按固定大小和重叠分块，适用于没有标题结构的文本
    """

    def __init__(self, chunk_size: int = 512, chunk_overlap: int = 50):
        """
        初始化简单分块器

        Args:
            chunk_size: 分块大小
            chunk_overlap: 分块重叠大小
        """
        self.chunk_size = chunk_size
        self.chunk_overlap = min(chunk_overlap, chunk_size // 2)

    def parse(self, text: str) -> List[Dict[str, str]]:
        """
        分块文本

        Args:
            text: 文本内容

        Returns:
            分块列表
        """
        chunks = []

        # 按段落分割
        paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]

        current_chunk = ""
        current_size = 0

        for para in paragraphs:
            para_size = len(para)

            if current_size + para_size > self.chunk_size and current_chunk:
                chunks.append(current_chunk.strip())
                # 保留重叠部分
                overlap_text = current_chunk[-self.chunk_overlap:] if len(current_chunk) > self.chunk_overlap else current_chunk
                current_chunk = overlap_text + "\n\n" + para
                current_size = len(current_chunk)
            else:
                if current_chunk:
                    current_chunk += "\n\n" + para
                else:
                    current_chunk = para
                current_size = len(current_chunk)

        if current_chunk:
            chunks.append(current_chunk.strip())

        # 如果没有段落，按字符分割
        if not chunks:
            for i in range(0, len(text), self.chunk_size - self.chunk_overlap):
                chunks.append(text[i:i + self.chunk_size])

        return [{"title": "", "content": chunk} for chunk in chunks]


# 按标点符号分割的分块器
class PunctuationChunker:
    """
    按标点符号分块

    在句子边界分割，保持语义完整性
    """

    # 标点符号分割模式
    SPLIT_PATTERN = re.compile(r'.{1,%d}[。| \\.|！|;|；|!|\n]' )

    def __init__(self, chunk_size: int = 256):
        """
        初始化标点分块器

        Args:
            chunk_size: 目标分块大小
        """
        self.chunk_size = chunk_size
        self.split_pattern = re.compile(r'.{1,%d}[。| \\.|！|;|；|!|?\n]' % chunk_size)
        self.max_pattern = re.compile(r'.{1,%d}' % chunk_size)

    def parse(self, text: str) -> List[Dict[str, str]]:
        """
        按标点分块

        Args:
            text: 文本内容

        Returns:
            分块列表
        """
        result = []

        for chunk in self._split_by_chunks(text):
            # 使用标点模式分割
            chunk_result = self.split_pattern.findall(chunk, flags=re.DOTALL)
            for c in chunk_result:
                if c.strip():
                    result.append(c.strip())

            # 处理剩余的长文本
            other_chunks = self.split_pattern.split(chunk, flags=re.DOTALL)
            for other in other_chunks:
                if len(other) > 0:
                    if len(other) < self.chunk_size:
                        if other.strip():
                            result.append(other.strip())
                    else:
                        # 强制分割
                        max_chunks = self.max_pattern.findall(other, flags=re.DOTALL)
                        for mc in max_chunks:
                            if mc.strip():
                                result.append(mc.strip())

        return [{"title": "", "content": c} for c in result if c.strip()]

    def _split_by_chunks(self, text: str, size: int = 10000) -> List[str]:
        """将长文本分割成适合处理的大块"""
        chunks = []
        for i in range(0, len(text), size):
            chunks.append(text[i:i + size])
        return chunks


# 便捷函数
def smart_chunk(
    text: str,
    chunk_size: int = 512,
    pattern: str = "smart"
) -> List[Dict[str, str]]:
    """
    智能分块

    Args:
        text: 文本内容
        chunk_size: 分块大小
        pattern: 分块模式 ("smart", "simple", "punctuation")

    Returns:
        分块列表
    """
    if pattern == "smart":
        chunker = SmartChunker(limit=chunk_size)
    elif pattern == "simple":
        chunker = SimpleChunker(chunk_size=chunk_size)
    elif pattern == "punctuation":
        chunker = PunctuationChunker(chunk_size=chunk_size)
    else:
        chunker = SmartChunker(limit=chunk_size)

    return chunker.parse(text)
