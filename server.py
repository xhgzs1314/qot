import json
import socketserver
from http.server import SimpleHTTPRequestHandler
import os
import subprocess
from urllib.parse import parse_qs, urlparse, unquote
import sys
from functools import lru_cache
class PHPLocalServer:
    def __init__(self, host='localhost', port=8080):
        self.host = host
        self.port = port
        self.base_dir = os.path.dirname(os.path.abspath(__file__))
        self.php_interpreter = self._get_php_interpreter_path()
        self.server = None
        self._validate_php_interpreter()

    def _get_php_interpreter_path(self):
        php_path = os.path.join(self.base_dir, 'env', 'php', 'php')
        if os.name == 'nt':
            php_path += '.exe'
        return php_path

    def _validate_php_interpreter(self):
        if not os.path.exists(self.php_interpreter) or not os.path.isfile(self.php_interpreter):
            print(f"错误: 未找到PHP解释器在 {self.php_interpreter}")
            sys.exit(1)

    def set_port(self, port):
        self.port = port

    def set_host(self, host):
        self.host = host

    def set_base_dir(self, base_dir):
        self.base_dir = os.path.abspath(base_dir)

    class _CustomHandler(SimpleHTTPRequestHandler):
        server_version = "PHPLocalServer/1.0"
        sys_version = ""
        _mime_types = {
            '.html': 'text/html',
            '.php': 'text/html',
            '.css': 'text/css',
            '.js': 'application/javascript',
            '.json': 'application/json',
            '.png': 'image/png',
            '.jpg': 'image/jpeg',
            '.gif': 'image/gif',
            '.txt': 'text/plain'
        }

        def __init__(self, *args, base_dir=None, php_interpreter=None, **kwargs):
            self.base_dir = base_dir
            self.php_interpreter = php_interpreter
            self.abs_base_dir = os.path.abspath(base_dir)
            super().__init__(*args, **kwargs)
        def do_PUT(self):
            try:
                parsed_path = urlparse(self.path)
                if parsed_path.path == '/api/save-file':
                    content_length = int(self.headers.get('Content-Length', 0))
                    data = self.rfile.read(content_length).decode('utf-8')
                    try:
                        payload = json.loads(data)
                        file_path = os.path.join(self.abs_base_dir, payload['path'].lstrip('/'))
                        os.makedirs(os.path.dirname(file_path), exist_ok=True)
                        with open(file_path, 'w', encoding='utf-8') as f:
                            f.write(payload['content'])
                
                        self.send_response(200)
                        self.send_header('Content-type', 'application/json')
                        self.end_headers()
                        self.wfile.write(json.dumps({
                            'status': 'success',
                            'message': 'File saved successfully'
                        }).encode('utf-8'))
                        return
                    except Exception as e:
                        self.send_error(500, f"Save failed: {str(e)}")
                        return
            except Exception as e:
                self.send_error(500, f"Server error: {str(e)}")
        def do_GET(self):
            parsed_path = urlparse(self.path)
            if parsed_path.path == '/api/open-file':
                try:
                    query_params = parse_qs(parsed_path.query)
                    if 'path' not in query_params:
                        self.send_error(400, "Missing path parameter")
                        return
                
                    file_path = os.path.join(self.abs_base_dir, query_params['path'][0].lstrip('/'))
                    if not os.path.isfile(file_path):
                        self.send_error(404, "File not found")
                        return
                    with open(file_path, 'r', encoding='utf-8') as f:
                        content = f.read()
                
                    self.send_response(200)
                    self.send_header('Content-type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({
                        'status': 'success',
                        'content': content,
                        'path': query_params['path'][0]
                    }).encode('utf-8'))
                    return
                except Exception as e:
                    self.send_error(500, f"Open failed: {str(e)}")
                    return
            elif parsed_path.path == '/api/file-explorer':
                try:
                    query_params = parse_qs(parsed_path.query)
                    dir_path = query_params.get('path', ['/'])[0]
                    full_path = os.path.join(self.abs_base_dir, dir_path.lstrip('/'))
                    if not os.path.isdir(full_path):
                        self.send_error(404, "Directory not found")
                        return
                    items = []
                    for item in os.listdir(full_path):
                        item_path = os.path.join(full_path, item)
                        items.append({
                            'name': item,
                            'path': os.path.join(dir_path, item),
                            'is_dir': os.path.isdir(item_path),
                            'modified': os.path.getmtime(item_path)
                        })
                
                    self.send_response(200)
                    self.send_header('Content-type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({
                        'status': 'success',
                        'items': items,
                        'current_path': dir_path
                    }).encode('utf-8'))
                    return
                except Exception as e:
                    self.send_error(500, f"File explorer failed: {str(e)}")
                    return
            self._process_request()

        def do_POST(self):
            self._process_request()

        @lru_cache(maxsize=100)  
        def _resolve_request_path(self):
            parsed_path = urlparse(self.path)
            path = unquote(parsed_path.path)    
            local_path = os.path.join(self.abs_base_dir, path.lstrip('/'))
            if not local_path.startswith(self.abs_base_dir):
                return "__invalid_path__"
                
            if not os.path.exists(local_path):
                return path  
                
            if os.path.isdir(local_path):
                if not path.endswith('/'):
                    return f"__redirect__{path}/"
                index_php = os.path.join(local_path, 'index.php')
                if os.path.exists(index_php) and os.path.isfile(index_php):
                    return f"{path}index.php"
                    
                index_html = os.path.join(local_path, 'index.html')
                if os.path.exists(index_html) and os.path.isfile(index_html):
                    return f"{path}index.html"
                    
                return "__show_server_ok__"
                
            elif os.path.isfile(local_path):
                return path
                
            return path

        def _process_request(self):
            try:
                resolved_path = self._resolve_request_path()
                if resolved_path == "__show_server_ok__":
                    self.send_response(200)
                    self.send_header('Content-type', 'text/plain; charset=utf-8')
                    self.end_headers()
                    self.wfile.write(b"Server is ok")
                    return
                    
                if resolved_path.startswith("__redirect__"):
                    redirect_path = resolved_path[11:]
                    self.send_response(301)
                    self.send_header('Location', redirect_path)
                    self.end_headers()
                    return
                    
                if resolved_path == "__invalid_path__":
                    self.send_error(403, "Forbidden")
                    return
                if resolved_path.endswith('.php'):
                    self._handle_php(resolved_path)
                    return
                self.path = resolved_path
                super().do_GET()
                
            except Exception as e:
                self.send_error(500, f"服务器错误: {str(e)}")
        def _get_post_vars(self, post_data):
            post_vars = {}
            if self.command == 'POST' and post_data:
                content_type = self.headers.get('Content-Type', '')
                if 'application/x-www-form-urlencoded' in content_type:
                    parsed = parse_qs(post_data.decode('utf-8'))
                    for key, values in parsed.items():
                        post_vars[f'POST_{key}'] = values[0]
                elif 'application/json' in content_type:
                    try:
                        data = json.loads(post_data.decode('utf-8'))
                        for key, value in data.items():
                            post_vars[f'POST_{key}'] = str(value)
                    except json.JSONDecodeError:
                        pass
            return post_vars
        def _handle_php(self, file_path):
            local_path = os.path.join(self.abs_base_dir, file_path.lstrip('/'))
            post_data = b""
            if self.command == 'POST':
                content_length = int(self.headers.get('Content-Length', 0))
                if content_length > 0:
                    post_data = self.rfile.read(content_length)
            parsed_url = urlparse(self.path)
            query_params = parse_qs(parsed_url.query)
            env = os.environ.copy()
            env.update({
                'REQUEST_METHOD': self.command,
                'REQUEST_URI': self.path,
                'QUERY_STRING': parsed_url.query,
                'SERVER_NAME': self.server.server_address[0],
                'SERVER_PORT': str(self.server.server_address[1]),
                'REMOTE_ADDR': self.client_address[0],
                'CONTENT_LENGTH': self.headers.get('Content-Length', '0'),
                'CONTENT_TYPE': self.headers.get('Content-Type', ''),
                'GATEWAY_INTERFACE': 'CGI/1.1',
                'SERVER_PROTOCOL': self.request_version,
                'PHP_SELF': file_path,
                'SCRIPT_FILENAME': local_path,
                'REDIRECT_STATUS': '200',
                'SCRIPT_NAME': file_path,
                'DOCUMENT_ROOT': self.abs_base_dir
            })
            try:
                stdin = subprocess.PIPE if post_data else None
                process = subprocess.Popen(
                    [self.php_interpreter, '-f', local_path, '--', parsed_url.query],
                    stdin=stdin,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=env,
                    close_fds=True
                )
        
                stdout, stderr = process.communicate(input=post_data)
        
                if stderr:
                    print(f"PHP错误: {stderr.decode('utf-8', errors='replace')}")


                content_type = 'text/html; charset=utf-8'
                for line in stdout.split(b'\r\n'):
                    if line.startswith(b'Content-Type:'):
                        content_type = line[13:].strip().decode('utf-8', errors='replace')
                        break

                self.send_response(200)
                self.send_header('Content-type', content_type)
                self.end_headers()
                self.wfile.write(stdout)
        
            except Exception as e:
                self.send_error(500, f"PHP执行错误: {str(e)}")

        def translate_path(self, path):
            path = super().translate_path(path)
            return os.path.join(self.abs_base_dir, os.path.relpath(path, os.getcwd()))

        def log_message(self, format, *args):
            return

    def start(self):
        server_address = (self.host, self.port)
        handler = lambda *args, **kwargs: self._CustomHandler(
            *args, 
            base_dir=self.base_dir, 
            php_interpreter=self.php_interpreter, 
            **kwargs
        )
        class ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
            allow_reuse_address = True
            
        self.server = ThreadedTCPServer(server_address, handler)
        
        print(f"服务器已启动，地址: http://{self.host}:{self.port}")
        print("按 Ctrl+C 停止服务器")
        
        try:
            self.server.serve_forever()
        except KeyboardInterrupt:
            self.stop()

    def stop(self):
        if self.server:
            print("\n服务器正在停止...")
            self.server.shutdown()
            self.server.server_close()
            print("服务器已停止")