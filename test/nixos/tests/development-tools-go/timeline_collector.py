# SPDX-License-Identifier: MPL-2.0
from http.server import BaseHTTPRequestHandler,HTTPServer
from pathlib import Path
import re
import sys

root=Path(sys.argv[1]);root.mkdir(parents=True,exist_ok=True)
class Collector(BaseHTTPRequestHandler):
    def do_POST(self):
        name=self.path.removeprefix('/')
        length=int(self.headers.get('Content-Length','0'))
        if not re.fullmatch(r'[A-Za-z0-9_-]+\.(bin|child\.log)',name) or not 0<=length<16*1024*1024 or (length==0 and not name.endswith('.child.log')):
            self.send_error(400);return
        data=self.rfile.read(length)
        if len(data)!=length:self.send_error(400);return
        (root/name).write_bytes(data)
        self.send_response(201);self.end_headers();self.wfile.write(b'OK')
    def log_message(self,format,*args):
        print(format%args,flush=True)
HTTPServer(('127.0.0.1',18781),Collector).serve_forever()
