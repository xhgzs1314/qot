import asyncio
import datetime
import io
import json
import sys
import os
from pathlib import Path
import threading
from typing import Optional, Callable, List
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

class PrintCapture:
    def __init__(
        self,
        app: Optional[FastAPI] = None,
        mount_path: str = "",
        html_template_path: Optional[str] = None,
        host: str = "0.0.0.0",
        port: int = 8000,
        enable_demo: bool = False,
        demo_interval: float = 2.0,
        on_message: Optional[Callable[[str], None]] = None
    ):
        self.connection_event = threading.Event()
        self.script_done_event = threading.Event()
        self.ready = False
        self.app = app or FastAPI()
        self.mount_path = mount_path.rstrip("/")
        self.host = host
        self.port = port
        self.enable_demo = enable_demo
        self.demo_interval = demo_interval
        self.on_message = on_message
        self.active_connections: List[WebSocket] = []
        self.ws_stream: Optional[WebSocketStream] = None
        self.original_stdout = None
        self._script_thread: Optional[threading.Thread] = None
        self._server_thread: Optional[threading.Thread] = None
        self._server_started_event = threading.Event()
        self._loop = None
        self._loop_ready = threading.Event()
        
        if html_template_path:
            self.html_template_path = Path(html_template_path)
        else:
            self.html_template_path = Path(__file__).parent / "html" / "index.html"
        self.html_content = self._load_html_template()
        self._register_routes()

    def _load_html_template(self) -> str:
        if self.html_template_path.exists():
            with open(self.html_template_path, "r", encoding="utf-8") as f:
                return f.read()
        else:
            return """
            <!DOCTYPE html>
            <html>
            <head><title>输出核心缺失</title></head>
            <body>
                <h1>输出核心缺失</h1>
                <p>输出核心缺失: {path}</p>
                <p>请确保plugin/myconsole/index.html文件存在</p>
            </body>
            </html>
            """.format(path=self.html_template_path)

    def _register_routes(self):
        @self.app.get(f"{self.mount_path}/")
        async def get_page():
            return HTMLResponse(self.html_content)

        @self.app.websocket(f"{self.mount_path}/ws")
        async def websocket_endpoint(websocket: WebSocket):
            await websocket.accept()
            self.active_connections.append(websocket)
            try:
                self._loop = asyncio.get_running_loop()
                self._loop_ready.set()
            except RuntimeError:
                pass
            
            if not self.ready:
                self.ready = True
                self.connection_event.set()
            
            try:
                while True:
                    data = await websocket.receive_text()
            except WebSocketDisconnect:
                if websocket in self.active_connections:
                    self.active_connections.remove(websocket)

    def wait_for_connection(self, timeout=None):
        return self.connection_event.wait(timeout)

    def broadcast_sync(self, message: str, level: str = "info", source: str = "script"):
        if not message:
            return
        structured_msg = {
            "type": "output",
            "data": message.strip(),
            "timestamp": datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3],
            "level": level,
            "source": source
        }
        json_msg = json.dumps(structured_msg, ensure_ascii=False)
        loop = self._loop
        if loop is None:
            try:
                loop = asyncio.get_event_loop()
                self._loop = loop
            except RuntimeError:
                return  
        
        if loop is not None and not loop.is_closed():
            connections = self.active_connections[:]
            for connection in connections:
                try:
                    asyncio.run_coroutine_threadsafe(
                        connection.send_text(json_msg),
                        loop
                    )
                except Exception:
                    pass

    def set_script(self, script_func: Callable):
        def script_wrapper():
            self._server_started_event.wait()
            self.wait_for_connection()
            self._loop_ready.wait(timeout=5)
            if self.ws_stream is None:
                self.ws_stream = WebSocketStream(self)
            self.original_stdout = sys.stdout
            sys.stdout = self.ws_stream
            
            try:
                print("[系统] 服务器已连接，开始执行脚本")
                script_func()
                
            except Exception as e:
                import traceback
                error_msg = traceback.format_exc()
                print(f"[错误] 脚本执行失败: {e}")
                for line in error_msg.split('\n'):
                    if line.strip():
                        print(f"[错误] {line}")
            finally:
                print("[系统] 脚本执行完毕")
                sys.stdout = self.original_stdout
                self.script_done_event.set()
        
        self._script_thread = threading.Thread(target=script_wrapper, daemon=True)

    def run(self, **kwargs):
        import uvicorn
        
        config = uvicorn.Config(
            app=self.app,
            host=self.host,
            port=self.port,
            loop="asyncio",
            **kwargs
        )
        server = uvicorn.Server(config)
        self._server_started_event.set()
        if self._script_thread:
            self._script_thread.start()
        
        try:
            server.run()
        except KeyboardInterrupt:
            print("\n[系统] 收到中断信号，正在关闭...")
            self.restore()
    
    def restore(self):
        if self.original_stdout:
            sys.stdout = self.original_stdout
            self.original_stdout = None


class WebSocketStream(io.StringIO):
    def __init__(self, capture: PrintCapture):
        super().__init__()
        self.capture = capture
    
    def write(self, s):
        super().write(s)
        if s and s.strip():
            msg = s.strip()
            level = "info"
            source = "script"
            if "[错误]" in msg or "错误:" in msg:
                level = "error"
                source = "error"
            elif "[警告]" in msg or "警告:" in msg:
                level = "warn"
            elif "[系统]" in msg:
                source = "system"
            self.capture.broadcast_sync(msg, level=level, source=source)
        
        return len(s)
    
    def flush(self):
        pass