"""hwime 识别引擎——纯逻辑包，不依赖任何 GUI 框架。

模块一览：
- ink      笔迹数据结构与渲染（双路共用）
- segment  连续书写笔迹的单字分割
- route_a  在线笔迹 CNN（cnn_chinese_hw 预训权重）
- route_b  笔迹渲染成图 → PP-OCR rec 整行识别
- fuse     双路融合 + n-gram/词频解码
"""
