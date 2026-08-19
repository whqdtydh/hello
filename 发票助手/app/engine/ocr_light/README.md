# ocr_light：RapidOCR 收编模块（轻量化 OCR）

本项目内的轻量 OCR 引擎，收编自开源项目 **RapidOCR**（Apache License 2.0，
作者 SWHL，https://github.com/RapidAI/RapidOCR），仅做以下适配：

1. 包名由 `rapidocr_onnxruntime` 改为 `app.engine.ocr_light`，
   子模块中的绝对导入（`from rapidocr_onnxruntime.utils import ...`）改为相对导入；
2. 保留原 PP-OCRv4 检测/识别模型与 cv2 预处理链路（识别精度与原版一致）；
3. 模型文件位于本包 `models/` 目录，路径按包内相对路径自动解析。

用途：`app/engine/pdf_service.py` 中对无文本层（图片型/扫描件）PDF 的
降级 OCR，识别发票票面金额、日期、铁路客票信息。

原始 LICENSE（Apache-2.0）见本目录 `LICENSE.rapidocr`。
