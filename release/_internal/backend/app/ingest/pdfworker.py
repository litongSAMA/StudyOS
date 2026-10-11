"""Native PDF decoding in a child process, so native crashes cannot kill the API."""
import json
import sys

try:
    import pymupdf
    with pymupdf.open(sys.argv[1]) as doc:
        pages=doc.page_count
        parts=[];remaining=200_000;text_pages=0
        for page in doc:
            value=page.get_text()[:remaining]
            if value.strip(): text_pages+=1
            parts.append(value);remaining-=len(value)
            if remaining<=0: break
        text='\n'.join(parts).strip()[:200_000]
        # 文本层判定：整体过短，或「平均每页不到 20 字」（多页扫描件里夹几行页眉）
        needs_ocr=len(text)<50 or (pages>1 and len(text)<20*pages)
        result={'text':text,'needs_ocr':needs_ocr,'pages':pages,'text_pages':text_pages,'text_chars':len(text)}
except Exception:
    result={'text':'','needs_ocr':False,'pages':0,'text_pages':0,'text_chars':0}
print(json.dumps(result,ensure_ascii=False))
