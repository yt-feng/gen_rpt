import re
import zipfile
import xml.etree.ElementTree as ET
from io import BytesIO
from typing import Dict, Any, Tuple
from bs4 import BeautifulSoup
import markdown

def extract_txt(file_bytes: bytes) -> Tuple[str, Dict[str, Any]]:
    text = file_bytes.decode("utf-8", errors="ignore")
    char_count = len(text)
    word_count = len(text.split())
    token_estimate = int(char_count / 4)
    metadata = {
        "character_count": char_count,
        "word_count": word_count,
        "token_estimate": token_estimate,
        "page_count": 1,
        "headings": [],
        "sections": []
    }
    return text, metadata

def extract_md(file_bytes: bytes) -> Tuple[str, Dict[str, Any]]:
    text = file_bytes.decode("utf-8", errors="ignore")
    # Parse markdown to HTML to get a feel of the structure, but we can also use regex for headings
    html = markdown.markdown(text)
    soup = BeautifulSoup(html, "html.parser")
    plain_text = soup.get_text()
    
    # Extract headings hierarchy
    headings = []
    for line in text.split("\n"):
        match = re.match(r"^(#{1,6})\s+(.*)$", line)
        if match:
            level = len(match.group(1))
            title = match.group(2).strip()
            headings.append({"level": level, "title": title})
            
    char_count = len(plain_text)
    word_count = len(plain_text.split())
    token_estimate = int(char_count / 4)
    
    metadata = {
        "character_count": char_count,
        "word_count": word_count,
        "token_estimate": token_estimate,
        "page_count": 1,
        "headings": headings,
        "sections": [h["title"] for h in headings]
    }
    return plain_text, metadata

def extract_html(file_bytes: bytes) -> Tuple[str, Dict[str, Any]]:
    html = file_bytes.decode("utf-8", errors="ignore")
    soup = BeautifulSoup(html, "html.parser")
    plain_text = soup.get_text()
    
    # Extract headings
    headings = []
    for h_tag in ["h1", "h2", "h3", "h4", "h5", "h6"]:
        for elem in soup.find_all(h_tag):
            level = int(h_tag[1])
            headings.append({"level": level, "title": elem.get_text().strip()})
            
    char_count = len(plain_text)
    word_count = len(plain_text.split())
    token_estimate = int(char_count / 4)
    
    metadata = {
        "character_count": char_count,
        "word_count": word_count,
        "token_estimate": token_estimate,
        "page_count": 1,
        "headings": headings,
        "sections": [h["title"] for h in headings]
    }
    return plain_text, metadata

def extract_docx(file_bytes: bytes) -> Tuple[str, Dict[str, Any]]:
    try:
        with zipfile.ZipFile(BytesIO(file_bytes)) as docx:
            xml_content = docx.read("word/document.xml")
            root = ET.fromstring(xml_content)
            namespaces = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
            text_parts = []
            headings = []
            
            # Simple outline extraction while looping XML
            for paragraph in root.findall(".//w:p", namespaces):
                p_text_parts = []
                for elem in paragraph.findall(".//w:t", namespaces):
                    if elem.text:
                        p_text_parts.append(elem.text)
                
                if p_text_parts:
                    full_p_text = "".join(p_text_parts)
                    text_parts.append(full_p_text)
                    
                    # Detect headings via style properties if present
                    pPr = paragraph.find("w:pPr", namespaces)
                    if pPr is not None:
                        pStyle = pPr.find("w:pStyle", namespaces)
                        if pStyle is not None:
                            val = pStyle.get("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}val")
                            if val and ("Heading" in val or "heading" in val.lower()):
                                try:
                                    level = int(re.search(r"\d+", val).group())
                                except Exception:
                                    level = 1
                                headings.append({"level": level, "title": full_p_text.strip()})
            
            plain_text = "\n".join(text_parts)
    except Exception:
        plain_text = file_bytes.decode("utf-8", errors="ignore")
        headings = []

    char_count = len(plain_text)
    word_count = len(plain_text.split())
    token_estimate = int(char_count / 4)
    
    metadata = {
        "character_count": char_count,
        "word_count": word_count,
        "token_estimate": token_estimate,
        "page_count": 1,
        "headings": headings,
        "sections": [h["title"] for h in headings]
    }
    return plain_text, metadata

def extract_pdf(file_bytes: bytes) -> Tuple[str, Dict[str, Any]]:
    # Fallback to UTF-8 decoding if not a real PDF
    if not file_bytes.startswith(b"%PDF"):
        return extract_txt(file_bytes)
        
    plain_text = ""
    # Try importing pypdf / PyPDF2
    try:
        import pypdf
        reader = pypdf.PdfReader(BytesIO(file_bytes))
        pages = []
        for page in reader.pages:
            t = page.extract_text()
            if t:
                pages.append(t)
        plain_text = "\n".join(pages)
        page_count = len(reader.pages)
    except ImportError:
        try:
            import PyPDF2
            reader = PyPDF2.PdfReader(BytesIO(file_bytes))
            pages = []
            for i in range(len(reader.pages)):
                t = reader.pages[i].extract_text()
                if t:
                    pages.append(t)
            plain_text = "\n".join(pages)
            page_count = len(reader.pages)
        except ImportError:
            # Low-level TJ/Tj regex scanner fallback
            text_parts = []
            for match in re.finditer(b"\\((.*?)\\)\\s*Tj", file_bytes):
                try:
                    text_parts.append(match.group(1).decode("utf-8", errors="ignore"))
                except Exception:
                    pass
            if text_parts:
                plain_text = "\n".join(text_parts)
            else:
                plain_text = file_bytes.decode("ascii", errors="ignore")
                # Filter printable text lines
                plain_text = "\n".join([line for line in plain_text.split("\n") if len(line.strip()) > 10])
            
            # Simple page count estimator
            page_count = max(1, file_bytes.count(b"/Type /Page"))
            
    char_count = len(plain_text)
    word_count = len(plain_text.split())
    token_estimate = int(char_count / 4)
    
    metadata = {
        "character_count": char_count,
        "word_count": word_count,
        "token_estimate": token_estimate,
        "page_count": page_count,
        "headings": [],
        "sections": []
    }
    return plain_text, metadata

def extract_document_text(file_bytes: bytes, extension: str) -> Tuple[str, Dict[str, Any]]:
    ext = extension.lower().lstrip(".")
    if ext == "txt":
        return extract_txt(file_bytes)
    elif ext == "md":
        return extract_md(file_bytes)
    elif ext == "html" or ext == "htm":
        return extract_html(file_bytes)
    elif ext == "docx":
        return extract_docx(file_bytes)
    elif ext == "pdf":
        return extract_pdf(file_bytes)
    else:
        # Graceful fallback
        return extract_txt(file_bytes)
