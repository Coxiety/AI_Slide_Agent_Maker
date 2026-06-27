"""
agent_designer.py — AGENT 3 · DESIGNER  (Phụ trách: Thành viên C)
================================================================
NHIỆM VỤ (ứng với yêu cầu "Tạo bố cục trình chiếu"):
    - Nhận nội dung đầy đủ (Deck) từ ContentAgent.
    - Với mỗi slide: TỰ QUYẾT có cần tìm ảnh minh hoạ không, rồi tải ảnh
      (qua ImageFetcher, có fallback placeholder).
    - Dựng file .pptx với layout full-bleed (qua SlideGenerator).
    - Quản lý phiên bản file (_ver_1, _ver_2, ...).

INPUT : schema.Deck  (+ thư mục output, tên file gốc)
OUTPUT: đường dẫn file .pptx

Tính "agent": tự ra quyết định ảnh theo từng slide & theo cấu hình use_images;
chịu trách nhiệm toàn bộ khâu trình bày, tách biệt khỏi khâu nội dung.
"""

from __future__ import annotations
import os
import re

from schema import Deck
from comfyui_fetcher import ComfyUIFetcher
from slide_generator import SlideGenerator
import llm


class DesignerAgent:
    def __init__(self, use_images: bool = True, model_name: str = "llama3.1:8b", api_url: str = llm.GEN_API):
        self.use_images = use_images
        self.model_name = model_name
        self.api_url = api_url
        self.fetcher = ComfyUIFetcher()
        self.sg = SlideGenerator()

    # ── API công khai ────────────────────────────────────────────────────────
    def build(self, deck: Deck, output_dir: str = "Product",
              base_name: str = "presentation", on_progress=None) -> str:
        def emit(msg):
            print(msg)
            if on_progress:
                try:
                    on_progress(msg)
                except Exception:
                    pass

        os.makedirs(output_dir, exist_ok=True)
        emit(f"[Designer] Dựng {len(deck.slides)} slide "
             f"(ảnh: {'BẬT' if self.use_images else 'TẮT'})...")

        # 1) Slide tiêu đề -----------------------------------------------------
        title_img = None
        if self.use_images:
            tq = deck.title if deck.title.isascii() else "technology innovation concept"
            emit("[Designer] Tìm ảnh bìa...")
            title_img = self.fetcher.fetch(tq)
        self.sg.add_title_slide(deck.title, deck.subtitle, title_img)

        # 2) Các slide nội dung (Tối ưu đa luồng) ------------------------------
        total = len(deck.slides)
        emit(f"[Designer] Đang nạp hàng chờ sinh ảnh cho {total} slide lên ComfyUI...")

        import concurrent.futures

        def process_slide(slide_info):
            idx, slide = slide_info
            emit(f"    [Thread] Bắt đầu xử lý Slide {idx}/{total}: {slide.title}")
            img = self._decide_image(slide, deck.title, emit)
            return slide, img

        slide_infos = list(enumerate(deck.slides, 1))
        processed_slides = []
        
        # Chạy đồng thời LLM Keyword và ComfyUI WebSocket
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            # map trả về kết quả theo ĐÚNG THỨ TỰ đầu vào
            results = executor.map(process_slide, slide_infos)
            for idx, (slide, img) in enumerate(results, 1):
                emit(f"[Designer] Đã có ảnh Slide {idx}/{total}: {slide.title}")
                processed_slides.append((slide, img))

        # 3) Lắp ráp ảnh vào PPTX tuần tự để tránh lỗi file
        for idx, (slide, img) in enumerate(processed_slides, 1):
            self.sg.add_content_slide(
                title=slide.title,
                bullet_points=slide.points,
                image_stream=img,
                deck_title=deck.title,
                page_num=idx + 1,
            )

        # 3) Lưu file (có versioning) -----------------------------------------
        suffix = self._next_version_suffix(output_dir, base_name)
        pptx_path = os.path.join(output_dir, f"{base_name}{suffix}.pptx")
        self.sg.save(pptx_path)
        emit(f"[Designer] ✔ Đã lưu: {pptx_path}")
        return pptx_path

    # ── Quyết định ảnh cho 1 slide ───────────────────────────────────────────
    def _decide_image(self, slide, deck_title: str, emit):
        if not self.use_images:
            return None
            
        # Ưu tiên sinh từ khóa kỹ thuật/an toàn bằng LLM
        query = self._generate_safe_keyword(slide, deck_title, emit)
        
        if not query:
            # Fallback về từ khóa cũ của Planner nếu LLM thất bại
            query = (slide.image_query or "").strip()
            
        if not query:
            return None
            
        img = self.fetcher.fetch(query)
        
        # Guard 2: Validate ảnh sau khi search
        if img and not self._vision_check_image(img, slide.title, emit):
            emit("    [!] Ảnh bị từ chối bởi Vision Model, chuyển sang placeholder.")
            from image_fetcher import _make_placeholder
            return _make_placeholder(query)
            
        return img

    def _vision_check_image(self, img_stream, slide_title, emit) -> bool:
        """
        Guard 2: Reject nếu ảnh không có liên quan kỹ thuật (dùng vision model check).
        (Placeholder: Cần model Llava hoặc GPT-4V để phân tích nội dung ảnh thực tế).
        """
        # TODO: Implement vision check using Ollama (llava)
        return True

    def _generate_safe_keyword(self, slide, deck_title: str, emit) -> str:
        """
        Sinh keyword tiếng Anh an toàn, trừu tượng/kỹ thuật bằng LLM.
        """
        # Hardcode an toàn cho slide Agenda và Kết luận (tránh ảnh kỹ thuật sai lệch như hamburger, trường học)
        title_lower = slide.title.lower()
        if "chương trình" in title_lower or "nghị sự" in title_lower or "mục lục" in title_lower:
            return "abstract presentation layout vector background"
        if "kết luận" in title_lower or "tổng kết" in title_lower or "hỏi đáp" in title_lower or "cảm ơn" in title_lower:
            return "abstract futuristic technology vector background"

        # Nếu slide ít nội dung, fallback
        if not slide.points or len(slide.points) == 0:
            return ""
            
        points_str = "\n".join(f"- {p}" for p in slide.points[:3])
        prompt = (
            "Bạn là một chuyên gia thiết kế và prompt engineer.\n"
            f"Chủ đề bài thuyết trình: '{deck_title}'\n"
            f"Tiêu đề slide hiện tại: '{slide.title}'\n"
            f"Nội dung slide:\n{points_str}\n\n"
            "NHIỆM VỤ: Tạo ra một cụm từ khóa tìm kiếm tiếng Anh (3-6 từ) để tìm ảnh minh họa nền (background) hoặc sơ đồ (diagram) cho slide này.\n"
            "QUY TẮC AN TOÀN NGHIÊM NGẶT (CONTENT SAFETY):\n"
            "1. TỪ CHỐI các từ dễ ra ảnh đời thực, con người, thể thao (ví dụ CẤM: 'development', 'run', 'athlete', 'man', 'human', 'people', 'team', 'working', 'nature', 'sport').\n"
            "2. CHỈ DÙNG các từ kỹ thuật, trừu tượng, công nghệ (ví dụ: 'network architecture', 'data flow diagram', 'algorithm concept', 'abstract technology background').\n"
            "3. CHỈ in ra cụm từ khóa bằng TIẾNG ANH, KHÔNG giải thích, KHÔNG có dấu ngoặc kép, KHÔNG có câu chào."
        )
        try:
            response = llm.generate(prompt, self.model_name, self.api_url, num_predict=30, temperature=0.2)
            query = response.strip(' "\'\n\r.*')
            
            # Kiểm tra cơ bản
            if not query or len(query.split()) > 12 or "nhiệm vụ" in query.lower() or "quy tắc" in query.lower():
                return ""
                
            # Bộ lọc Content Safety cuối cùng (cắt bỏ từ cấm nếu lỡ sinh ra)
            forbidden = {"man", "woman", "human", "people", "person", "team", "working", "nature", "outdoor", "indoor", "office", "athlete", "run", "development", "sport", "progress", "growth"}
            clean_words = [w for w in query.lower().split() if w not in forbidden]
            
            final_query = " ".join(clean_words[:6])
            
            # Guard 1: Validate keyword output trước khi search
            invalid_phrases = ["cụm", "từ", "khóa", "keyword", "tìm", "kiếm", "tiếng", "bạn", "nhiệm", "vụ", "slide"]
            has_vietnamese = any(ord(c) > 127 for c in final_query) # Strict English check
            
            if len(final_query) < 10 or has_vietnamese or any(p in final_query.lower() for p in invalid_phrases):
                # Fallback: Trích xuất các từ tiếng Anh/ASCII từ tên slide và chủ đề
                safe_deck = " ".join([w for w in deck_title.split() if w.isascii() and w.isalnum()])
                safe_slide = " ".join([w for w in slide.title.split() if w.isascii() and w.isalnum()])
                
                fallback = f"{safe_deck} {safe_slide}".strip()
                if not fallback or len(fallback) < 3:
                    final_query = "abstract technology diagram"
                else:
                    final_query = f"{fallback} abstract diagram"
                    
                emit(f"    [Keyword] LLM lỗi/bị cắt cụt, dùng fallback: '{final_query}'")
            else:
                emit(f"    [Keyword] LLM tự sinh query: '{final_query}'")
                
            return final_query
            
        except Exception as e:
            emit(f"    [Designer] Lỗi sinh keyword: {e}")
            return ""

    # ── Đặt tên phiên bản ────────────────────────────────────────────────────
    @staticmethod
    def _next_version_suffix(product_dir: str, base_name: str) -> str:
        if not os.path.exists(os.path.join(product_dir, f"{base_name}.pptx")):
            return ""
        version = 1
        pattern = re.compile(rf"^{re.escape(base_name)}_ver_(\d+)\.pptx$")
        for fname in os.listdir(product_dir):
            m = pattern.match(fname)
            if m:
                version = max(version, int(m.group(1)) + 1)
        return f"_ver_{version}"


# ── Cho phép chạy/test ĐỘC LẬP agent này ─────────────────────────────────────
if __name__ == "__main__":
    import sys
    import json

    # Test độc lập: dựng PPTX từ 1 file *_content.json có sẵn,
    # KHÔNG cần Planner hay Content chạy trước.
    if len(sys.argv) > 1 and os.path.exists(sys.argv[1]):
        with open(sys.argv[1], "r", encoding="utf-8") as f:
            deck = Deck.from_dict(json.load(f))
        base = os.path.basename(sys.argv[1]).replace("_content.json", "")
    else:
        # Deck mẫu nếu không truyền file
        deck = Deck.from_dict({
            "title": "Demo DesignerAgent",
            "subtitle": "Dựng slide từ dữ liệu mẫu",
            "slides": [
                {"title": "Slide thử nghiệm",
                 "points": ["Đây là một ý nội dung mẫu để kiểm tra layout.",
                            "Designer chịu trách nhiệm trình bày và tìm ảnh."],
                 "image_query": "abstract technology background"},
            ],
        })
        base = "demo_designer"

    path = DesignerAgent(use_images=True).build(deck, "Product", base)
    print("OUTPUT:", path)
