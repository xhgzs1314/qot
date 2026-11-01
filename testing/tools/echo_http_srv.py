# -*- coding: utf-8 -*-
"""P0.3 hermetic HTTP 服务器（std_net 差分用例的本地服务）。

固定响应、循环 accept（由测试框架 teardown kill）。用法：
    python testing/tools/echo_http_srv.py <port>
"""
import socket
import sys


def main():
    port = int(sys.argv[1])
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(('127.0.0.1', port))
    s.listen(8)
    while True:
        c, _ = s.accept()
        try:
            c.recv(65536)
            c.sendall(b'HTTP/1.0 200 OK\r\n'
                      b'Content-Type: text/plain\r\n'
                      b'Connection: close\r\n'
                      b'\r\n'
                      b'qot-net-ok')
        except OSError:
            pass
        finally:
            try:
                c.close()
            except OSError:
                pass


main()
