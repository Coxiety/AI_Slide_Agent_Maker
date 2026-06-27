import os
import json
import random
import time
import urllib.request
import urllib.parse
import uuid
import websocket
from io import BytesIO

from image_fetcher import ImageFetcher

class ComfyUIFetcher:
    def __init__(self, server_address="127.0.0.1:8188"):
        self.server_address = server_address
        self.fallback_fetcher = ImageFetcher()
        self.workflow_template = {
            "3": {
                "inputs": {
                "seed": 650101271515995,
                "steps": 20,
                "cfg": 8,
                "sampler_name": "euler",
                "scheduler": "normal",
                "denoise": 1,
                "model": ["4", 0],
                "positive": ["6", 0],
                "negative": ["7", 0],
                "latent_image": ["5", 0]
                },
                "class_type": "KSampler",
            },
            "4": {
                "inputs": {"ckpt_name": "DreamShaper_8_pruned.safetensors"},
                "class_type": "CheckpointLoaderSimple",
            },
            "5": {
                "inputs": {"width": 1024, "height": 768, "batch_size": 1},
                "class_type": "EmptyLatentImage",
            },
            "6": {
                "inputs": {
                "text": "masterpiece, best quality, ultra-detailed, 8k, photorealistic oil painting, cinematic lighting, soft focus,\n1girl, solo, blonde wavy short hair, messy hair, floating hair, looking up, blue eyes, soft gaze, parted lips, pale skin, delicate facial features, **simple white long-sleeve top, fully covered shoulders, high neckline, modest clothing**, gold necklace with green gem pendant, upper body shot,\nbackground of cosmic space, giant glowing planet with clouds, bright light beam from planet, starry sky, bokeh, dreamy atmosphere, soft light, ethereal, fantasy aesthetic, depth of field, painterly details, smooth skin texture",
                "clip": ["4", 1]
                },
                "class_type": "CLIPTextEncode",
            },
            "7": {
                "inputs": {
                "text": "lowres, bad anatomy, bad hands, text, error, missing fingers, extra digit, fewer digits, cropped, worst quality, low quality, normal quality, jpeg artifacts, signature, watermark, username, blurry, artist name, deformed, disfigured, ugly, extra limbs, missing limbs, poorly drawn face, mutated, mutated hands, extra fingers, bad proportions, gross proportions, malformed limbs, missing arms, missing legs, extra arms, extra legs, fused fingers, long neck, text, signature, watermark, cartoon, anime, 3d render, realistic (non-painting style)",
                "clip": ["4", 1]
                },
                "class_type": "CLIPTextEncode",
            },
            "8": {
                "inputs": {"samples": ["3", 0], "vae": ["4", 2]},
                "class_type": "VAEDecode",
            },
            "9": {
                "inputs": {"filename_prefix": "ComfyUI", "images": ["8", 0]},
                "class_type": "SaveImage",
            }
        }

    def _queue_prompt(self, prompt_workflow, client_id):
        p = {"prompt": prompt_workflow, "client_id": client_id}
        data = json.dumps(p).encode('utf-8')
        req = urllib.request.Request(f"http://{self.server_address}/prompt", data=data)
        req.add_header('Content-Type', 'application/json')
        return json.loads(urllib.request.urlopen(req).read())

    def _get_history(self, prompt_id):
        with urllib.request.urlopen(f"http://{self.server_address}/history/{prompt_id}") as response:
            return json.loads(response.read())

    def _get_image(self, filename, subfolder, folder_type):
        data = {"filename": filename, "subfolder": subfolder, "type": folder_type}
        url_values = urllib.parse.urlencode(data)
        with urllib.request.urlopen(f"http://{self.server_address}/view?{url_values}") as response:
            return response.read()

    def fetch(self, query: str, timeout: int = 120) -> BytesIO:
        print(f"    [ComfyUI] Bắt đầu sinh ảnh cho: '{query[:50]}...'")
        
        import copy
        workflow = copy.deepcopy(self.workflow_template)
        
        style_prefix = "masterpiece, best quality, highly detailed, professional presentation graphic, clean composition, "
        workflow["6"]["inputs"]["text"] = style_prefix + query
        workflow["3"]["inputs"]["seed"] = random.randint(1, 1000000000000000)
        workflow["5"]["inputs"]["width"] = 1024
        workflow["5"]["inputs"]["height"] = 768

        client_id = str(uuid.uuid4())
        ws = None
        try:
            # Connect via WebSocket
            ws = websocket.WebSocket()
            ws.connect(f"ws://{self.server_address}/ws?clientId={client_id}")

            # Send prompt
            response = self._queue_prompt(workflow, client_id)
            prompt_id = response['prompt_id']

            print("    [ComfyUI] Đang chờ render", end="", flush=True)
            ws.settimeout(timeout)
            
            while True:
                out = ws.recv()
                if isinstance(out, str):
                    message = json.loads(out)
                    if message['type'] == 'executing':
                        data = message['data']
                        if data['node'] is None and data['prompt_id'] == prompt_id:
                            break # Execution is done
            
            print(" [Hoàn tất]")
            
            history = self._get_history(prompt_id)
            if prompt_id in history:
                result_images = []
                for node_id in history[prompt_id]['outputs']:
                    node_output = history[prompt_id]['outputs'][node_id]
                    if 'images' in node_output:
                        for image in node_output['images']:
                            image_data = self._get_image(image['filename'], image['subfolder'], image['type'])
                            result_images.append(image_data)
                
                if result_images:
                    return BytesIO(result_images[0])

        except Exception as e:
            print(f"\n    [!] Lỗi khi gọi ComfyUI API qua WebSocket: {e}")
            print("    [!] Tự động chuyển sang DuckDuckGo fallback...")
            return self.fallback_fetcher.fetch(query)
        finally:
            if ws:
                ws.close()
            
        return self.fallback_fetcher.fetch(query)

