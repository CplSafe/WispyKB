# PDF 解析服务
# 基于 MaxKB 的 PDF 解析实现，增强 PDF 解析能力
# 支持：TOC 目录解析、字体大小检测、内部链接解析、图片提取

import os
import re
import tempfile
import time
import traceback
from typing import List, Dict, Any, Optional
from collections import Counter
import logging

logger = logging.getLogger(__name__)

try:
    import fitz  # PyMuPDF
    PYMUPDF_AVAILABLE = True
except ImportError:
    PYMUPDF_AVAILABLE = False
    logger.warning("PyMuPDF 未安装，PDF 高级解析功能不可用: pip install PyMuPDF")


class PDFParser:
    """
    高级 PDF 解析器

    功能：
    1. PDF TOC（目录）解析 - 自动识别文档结构
    2. 字体大小检测 - 根据字体差异识别标题级别
    3. 内部链接解析 - 通过链接提取章节
    4. 图片提取 - 保留图片引用
    """

    # Markdown 标题正则模式
    DEFAULT_PATTERN_LIST = [
        re.compile('(?<=^)# .*|(?<=\\n)# .*'),
        re.compile('(?<=\\n)(?<!#)## (?!#).*|(?<=^)(?<!#)## (?!#).*'),
        re.compile("(?<=\\n)(?<!#)### (?!#).*|(?<=^)(?<!#)### (?!#).*"),
        re.compile("(?<=\\n)(?<!#)#### (?!#).*|(?<=^)(?<!#)#### (?!#).*"),
        re.compile("(?<=\\n)(?<!#)##### (?!#).*|(?<=^)(?<!#)##### (?!#).*"),
        re.compile("(?<=\\n)(?<!#)###### (?!#).*|(?<=^)(?<!#)###### (?!#).*"),
        re.compile("(?<!\\n)\\n\\n+")
    ]

    def __init__(self):
        self.available = PYMUPDF_AVAILABLE

    def parse(self, file_path: str, limit: int = 100000) -> Dict[str, Any]:
        """
        解析 PDF 文件

        Args:
            file_path: PDF 文件路径
            limit: 单个分块最大字符数

        Returns:
            解析结果 {"title": "章节标题", "content": "章节内容"} 列表
        """
        if not self.available:
            raise ValueError("PyMuPDF 未安装，请运行: pip install PyMuPDF")

        if not os.path.exists(file_path):
            raise FileNotFoundError(f"文件不存在: {file_path}")

        pdf_document = fitz.open(file_path)
        try:
            # 第一步：尝试解析 TOC 目录
            result = self._handle_toc(pdf_document, limit)
            if result is not None and len(result) > 0:
                logger.info(f"PDF 通过 TOC 解析成功，共 {len(result)} 个章节")
                return {"source": "toc", "content": result}

            # 第二步：尝试解析内部链接
            result = self._handle_links(pdf_document, limit)
            if result is not None and len(result) > 0:
                logger.info(f"PDF 通过链接解析成功，共 {len(result)} 个章节")
                return {"source": "links", "content": result}

            # 第三步：通过字体大小解析
            logger.info("PDF 无目录和链接，使用字体大小检测解析")
            content = self._handle_font_size(pdf_document, file_path)

            # 使用智能分块
            from .smart_chunk import SmartChunker
            chunker = SmartChunker(pattern_list=self.DEFAULT_PATTERN_LIST)
            chunks = chunker.parse(content, limit=limit)

            return {"source": "font_size", "content": chunks}

        except Exception as e:
            logger.error(f"PDF 解析失败: {e}, {traceback.format_exc()}")
            return {"source": "error", "content": []}
        finally:
            pdf_document.close()

    def _handle_toc(self, doc: fitz.Document, limit: int) -> Optional[List[Dict]]:
        """
        处理有目录的 PDF

        Args:
            doc: PyMuPDF 文档对象
            limit: 单个分块最大字符数

        Returns:
            章节列表或 None
        """
        toc = doc.get_toc()
        if toc is None or len(toc) == 0:
            return None

        chapters = []

        for i, entry in enumerate(toc):
            level, title, start_page = entry
            start_page -= 1  # PyMuPDF 页码从 0 开始

            # 确定结束页码
            if i + 1 < len(toc):
                end_page = toc[i + 1][2] - 1
            else:
                end_page = doc.page_count - 1

            # 清理标题
            title = self._clean_chapter_title(title)

            # 提取章节内容
            chapter_text = ""
            for page_num in range(start_page, min(end_page + 1, doc.page_count)):
                page = doc.load_page(page_num)
                text = page.get_text("text")
                # 清理换行
                text = re.sub(r'(?<!。)\\n+', '', text)
                text = re.sub(r'(?<!.)\\n+', '', text)

                # 移除标题本身
                idx = text.find(title)
                if idx > -1:
                    text = text[idx + len(title):]

                # 移除下一章节标题
                if i + 1 < len(toc):
                    next_title = self._clean_chapter_title(toc[i + 1][1])
                    idx = text.find(next_title)
                    if idx > -1:
                        text = text[:idx]

                chapter_text += text

            # 清理空字符
            chapter_text = chapter_text.replace('\0', '')

            # 限制标题长度
            real_title = title[:256]

            # 分割过长的章节
            if 0 < limit < len(chapter_text):
                split_texts = self._smart_split(chapter_text, limit)
                for text in split_texts:
                    chapters.append({"title": real_title, "content": text})
            else:
                chapters.append({
                    "title": real_title,
                    "content": chapter_text if chapter_text else real_title
                })

        return chapters

    def _handle_links(self, doc: fitz.Document, limit: int) -> Optional[List[Dict]]:
        """
        处理有内部链接的 PDF

        Args:
            doc: PyMuPDF 文档对象
            limit: 单个分块最大字符数

        Returns:
            章节列表或 None
        """
        if not self._check_links_in_pdf(doc):
            return None

        chapters = []
        toc_start_page = -1
        page_content = ""
        handle_pre_toc = True

        # 遍历每一页
        for page_num in range(doc.page_count):
            page = doc.load_page(page_num)
            links = page.get_links()

            if len(links) > 0 and toc_start_page < 0:
                toc_start_page = page_num

            if toc_start_page < 0:
                page_content += page.get_text('text')

            # 处理内部链接
            for num in range(len(links)):
                link = links[num]
                if link['kind'] != 1:  # 只处理内部链接
                    continue

                dest_page = link['page']
                rect = link['from']

                # 前言处理
                if dest_page < toc_start_page:
                    handle_pre_toc = False

                # 提取链接标题
                link_title = page.get_text("text", clip=rect).strip().split("\n")[0]
                link_title = re.sub(r'^[一二三四五六七八九十\s*]、\s*', '', link_title)
                link_title = re.sub(r'^第[一二三四五六七八九十]章\s*', '', link_title).strip()

                # 确定起止页面
                start_page = dest_page
                end_page = dest_page

                if num + 1 < len(links) and links[num + 1]['kind'] == 1:
                    next_rect = links[num + 1]['from']
                    next_title = page.get_text("text", clip=next_rect).strip().split("\n")[0]
                    end_page = links[num + 1]['page']
                else:
                    next_title = None

                # 提取章节内容
                chapter_text = ""
                for p_num in range(start_page, min(end_page + 1, doc.page_count)):
                    p = doc.load_page(p_num)
                    text = p.get_text("text")
                    text = re.sub(r'(?<!。)\\n+', '', text)
                    text = re.sub(r'(?<!.)\\n+', '', text)

                    idx = text.find(link_title)
                    if idx > -1:
                        text = text[idx + len(link_title):]

                    if next_title:
                        idx = text.find(next_title)
                        if idx > -1:
                            text = text[:idx]

                    chapter_text += text

                chapter_text = chapter_text.replace('\0', '')

                # 分割过长的章节
                if 0 < limit < len(chapter_text):
                    split_texts = self._smart_split(chapter_text, limit)
                    for text in split_texts:
                        chapters.append({"title": link_title, "content": text})
                else:
                    chapters.append({"title": link_title, "content": chapter_text})

        # 处理目录前的内容（前言等）
        if handle_pre_toc and page_content.strip():
            page_content = re.sub(r'(?<!。)\\n+', '', page_content)
            page_content = re.sub(r'(?<!.)\\n+', '', page_content)
            page_content = page_content.strip()

            if page_content:
                from .smart_chunk import SmartChunker
                chunker = SmartChunker(pattern_list=self.DEFAULT_PATTERN_LIST)
                pre_chunks = chunker.parse(page_content, limit=limit)
                chapters = pre_chunks + chapters

        return chapters if chapters else None

    def _handle_font_size(self, doc: fitz.Document, file_path: str) -> str:
        """
        通过字体大小解析 PDF（无目录时使用）

        原理：
        1. 收集所有字体大小，计算正文字体大小（众数）
        2. 根据与正文的差值判断标题级别
        3. 差值 > 2: H2 标题
        4. 差值 > 0.5: H3 标题
        5. 其他: 正文

        Args:
            doc: PyMuPDF 文档对象
            file_path: 文件路径（用于日志）

        Returns:
            解析后的 Markdown 文本
        """
        # 第一步：收集所有字体大小
        font_sizes = []
        for page_num in range(len(doc)):
            page = doc.load_page(page_num)
            blocks = page.get_text("dict")["blocks"]
            for block in blocks:
                if block["type"] == 0:  # 文本块
                    for line in block["lines"]:
                        for span in line["spans"]:
                            if span["size"] > 0:
                                font_sizes.append(span["size"])

        # 计算正文字体大小（众数）
        if not font_sizes:
            body_font_size = 12
        else:
            body_font_size = Counter(font_sizes).most_common(1)[0][0]

        logger.info(f"PDF 正文字体大小: {body_font_size}")

        # 第二步：提取内容并标记标题
        content = ""
        for page_num in range(len(doc)):
            start_time = time.time()
            page = doc.load_page(page_num)
            blocks = page.get_text("dict")["blocks"]

            for block in blocks:
                if block["type"] == 0:  # 文本块
                    for line in block["lines"]:
                        if not line["spans"]:
                            continue

                        text = "".join([span["text"] for span in line["spans"]])
                        font_size = line["spans"][0]["size"]

                        # 根据与正文的差值判断标题级别
                        size_diff = font_size - body_font_size

                        if size_diff > 2:  # 明显大于正文 -> H2
                            text = f"## {text}\n\n"
                        elif size_diff > 0.5:  # 略大于正文 -> H3
                            text = f"### {text}\n\n"
                        else:  # 正文
                            text = f"{text}\n"

                        content += text

                elif block["type"] == 1:  # 图片块
                    content += f"![image](image_{page_num}_{block['number']})\n\n"

            content = content.replace('\0', '')

            elapsed_time = time.time() - start_time
            logger.debug(f"PDF 页面 {page_num + 1} 处理时间: {elapsed_time:.3f}s")

        return content

    def _check_links_in_pdf(self, doc: fitz.Document) -> bool:
        """检查 PDF 是否包含内部链接"""
        for page_number in range(len(doc)):
            page = doc[page_number]
            links = page.get_links()
            if links:
                for link in links:
                    if link['kind'] == 1:  # 内部链接
                        return True
        return False

    def _clean_chapter_title(self, title: str) -> str:
        """清理章节标题"""
        title = re.sub(r'[一二三四五六七八九十\s*]、\s*', '', title)
        title = re.sub(r'第[一二三四五六七八九十]章\s*', '', title)
        return title.strip()

    def _smart_split(self, text: str, limit: int) -> List[str]:
        """
        智能分割文本

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
                ('。', 0), ('.', 0),  # 句号
                ('！', 0), ('!', 0),  # 感叹号
                ('？', 0), ('?', 0),  # 问号
                ('；', 0), (';', 0),  # 分号
                ('\n', 0),  # 换行
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


# 便捷函数
def parse_pdf(file_path: str, limit: int = 100000) -> List[Dict[str, str]]:
    """
    解析 PDF 文件

    Args:
        file_path: PDF 文件路径
        limit: 单个分块最大字符数

    Returns:
        {"title": "标题", "content": "内容"} 列表
    """
    parser = PDFParser()
    result = parser.parse(file_path, limit)
    return result.get("content", [])
