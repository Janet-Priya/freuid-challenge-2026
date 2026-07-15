from http.server import BaseHTTPRequestHandler, HTTPServer

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"hello from janet's backend")

server = HTTPServer(("localhost", 5000), handler)
print("Server running on port 5000...")
server.serve_forever()
