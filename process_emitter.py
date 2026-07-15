import asyncio
import websockets
import json
import os
import time
import pwd
import threading
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler

# ─────────────────────────────────────────────
# GLOBALS
# ─────────────────────────────────────────────
latest_snapshot = {}
connected_clients = set()
snapshot_lock = threading.Lock()
OUTPUT_FILE = os.path.expanduser("~/processes.json")
INTERVAL = 2

STATE_MAP = {
    'R': 'Running',
    'S': 'Sleeping',
    'D': 'Waiting (Disk IO)',
    'Z': 'Zombie',
    'T': 'Stopped',
    'I': 'Idle',
    'X': 'Dead',
    't': 'Tracing Stop',
}

# ─────────────────────────────────────────────
# /proc READERS
# ─────────────────────────────────────────────
def read_status(pid):
    try:
        data = {}
        with open(f'/proc/{pid}/status', 'r') as f:
            for line in f:
                parts = line.split(':', 1)
                if len(parts) == 2:
                    data[parts[0].strip()] = parts[1].strip()
        return data
    except:
        return None

def read_stat(pid):
    try:
        with open(f'/proc/{pid}/stat', 'r') as f:
            return f.read().split()
    except:
        return None

def read_cmdline(pid):
    try:
        with open(f'/proc/{pid}/cmdline', 'rb') as f:
            raw = f.read()
            return raw.replace(b'\x00', b' ').decode('utf-8', errors='replace').strip()
    except:
        return ''

def read_total_cpu():
    try:
        with open('/proc/stat', 'r') as f:
            line = f.readline()
            fields = line.split()
            total = sum(int(x) for x in fields[1:])
            return total
    except:
        return 0

def get_username(uid):
    try:
        return pwd.getpwuid(int(uid)).pw_name
    except:
        return str(uid)

def get_boot_time():
    try:
        with open('/proc/stat', 'r') as f:
            for line in f:
                if line.startswith('btime'):
                    return int(line.split()[1])
    except:
        pass
    return 0

BOOT_TIME = get_boot_time()
CLK_TCK = os.sysconf(os.sysconf_names['SC_CLK_TCK'])

# ─────────────────────────────────────────────
# CPU DELTA TRACKING
# ─────────────────────────────────────────────
prev_proc_cpu = {}
prev_total_cpu = 0
prev_time = time.time()

# ─────────────────────────────────────────────
# COLLECTOR
# ─────────────────────────────────────────────
def collect_processes():
    global prev_proc_cpu, prev_total_cpu, prev_time

    now = time.time()
    total_cpu = read_total_cpu()
    total_cpu_delta = total_cpu - prev_total_cpu
    processes = []

    # read total memory once
    try:
        with open('/proc/meminfo', 'r') as f:
            total_mem_kb = int(f.readline().split()[1])
    except:
        total_mem_kb = 1

    for entry in os.scandir('/proc'):
        if not entry.name.isdigit():
            continue

        pid = int(entry.name)
        status = read_status(pid)
        if not status:
            continue

        stat = read_stat(pid)
        if not stat:
            continue

        cmdline = read_cmdline(pid)

        # basic fields
        name = status.get('Name', 'unknown')
        state_code = status.get('State', 'S').split()[0]
        state = STATE_MAP.get(state_code, 'Unknown')
        ppid = int(status.get('PPid', 0))
        threads = int(status.get('Threads', 1))

        # memory
        try:
            mem_kb = int(status.get('VmRSS', '0 kB').split()[0])
            vm_size_kb = int(status.get('VmSize', '0 kB').split()[0])
        except:
            mem_kb = 0
            vm_size_kb = 0

        # nice
        try:
            nice = int(stat[18])
        except:
            nice = 0

        # user
        uid = status.get('Uid', '0').split()[0]
        user = get_username(uid)

        # start time
        try:
            start_ticks = int(stat[21])
            start_time = BOOT_TIME + (start_ticks / CLK_TCK)
            started = datetime.fromtimestamp(start_time).strftime('%H:%M:%S')
        except:
            started = 'unknown'

        # cpu
        try:
            proc_total = int(stat[13]) + int(stat[14])
        except:
            proc_total = 0

        prev = prev_proc_cpu.get(pid, proc_total)
        proc_delta = proc_total - prev
        prev_proc_cpu[pid] = proc_total

        if total_cpu_delta > 0:
            cpu_percent = round((proc_delta / total_cpu_delta) * 100 * os.cpu_count(), 2)
        else:
            cpu_percent = 0.0

        mem_percent = round((mem_kb / total_mem_kb) * 100, 2)

        processes.append({
            'pid': pid,
            'name': name,
            'state': state,
            'state_code': state_code,
            'cpu_percent': cpu_percent,
            'memory_kb': mem_kb,
            'virtual_memory_kb': vm_size_kb,
            'memory_percent': mem_percent,
            'nice': nice,
            'parent_pid': ppid,
            'threads': threads,
            'user': user,
            'cmd': cmdline if cmdline else f'[{name}]',
            'started': started,
        })

    prev_total_cpu = total_cpu
    prev_time = now

    processes.sort(key=lambda x: x['cpu_percent'], reverse=True)

    state_summary = {}
    for p in processes:
        s = p['state']
        state_summary[s] = state_summary.get(s, 0) + 1

    return {
        'timestamp': datetime.now().isoformat(),
        'total_processes': len(processes),
        'state_summary': state_summary,
        'processes': processes,
    }

# ─────────────────────────────────────────────
# SAVE TO FILE
# ─────────────────────────────────────────────
def save_to_file(snapshot):
    try:
        with open(OUTPUT_FILE, 'w') as f:
            json.dump(snapshot, f, indent=2)
    except Exception as e:
        print(f"Failed to save: {e}")

# ─────────────────────────────────────────────
# COLLECTOR LOOP
# ─────────────────────────────────────────────
async def collector_loop():
    global latest_snapshot
    print(f"Collecting every {INTERVAL}s → saving to {OUTPUT_FILE}")
    while True:
        try:
            snapshot = collect_processes()
            with snapshot_lock:
                latest_snapshot = snapshot
            save_to_file(snapshot)
            print(f"[{snapshot['timestamp']}] {snapshot['total_processes']} processes | {len(connected_clients)} clients connected")

            if connected_clients:
                message = json.dumps(snapshot)
                dead = set()
                for ws in connected_clients.copy():
                    try:
                        await ws.send(message)
                    except:
                        dead.add(ws)
                connected_clients.difference_update(dead)

        except Exception as e:
            print(f"Collector error: {e}")

        await asyncio.sleep(INTERVAL)

# ─────────────────────────────────────────────
# WEBSOCKET HANDLER
# ─────────────────────────────────────────────
async def websocket_handler(websocket):
    connected_clients.add(websocket)
    print(f"Client connected: {websocket.remote_address}")
    try:
        with snapshot_lock:
            if latest_snapshot:
                await websocket.send(json.dumps(latest_snapshot))
        await websocket.wait_closed()
    except Exception as e:
        print(f"Client error: {e}")
    finally:
        connected_clients.discard(websocket)
        print(f"Client disconnected: {websocket.remote_address}")

# ─────────────────────────────────────────────
# HTML UI
# ─────────────────────────────────────────────
HTML = """<!DOCTYPE html>
<html>
<head>
<title>Process Monitor</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { background: #0d1117; color: #e6edf3; font-family: monospace; font-size: 13px; }
  header { background: #161b22; padding: 16px 24px; border-bottom: 1px solid #30363d; display: flex; align-items: center; justify-content: space-between; }
  header h1 { font-size: 16px; color: #58a6ff; }
  .status { font-size: 12px; color: #8b949e; }
  .status span { color: #3fb950; }
  .stats { display: flex; gap: 16px; padding: 16px 24px; background: #161b22; border-bottom: 1px solid #30363d; flex-wrap: wrap; }
  .stat-box { background: #0d1117; border: 1px solid #30363d; border-radius: 6px; padding: 12px 20px; min-width: 140px; }
  .stat-box .label { font-size: 11px; color: #8b949e; margin-bottom: 4px; }
  .stat-box .value { font-size: 20px; font-weight: bold; }
  .filters { padding: 12px 24px; display: flex; gap: 12px; align-items: center; flex-wrap: wrap; border-bottom: 1px solid #21262d; }
  .filters input, .filters select { background: #161b22; border: 1px solid #30363d; color: #e6edf3; padding: 6px 12px; border-radius: 6px; font-family: monospace; font-size: 12px; }
  .filters input { width: 200px; }
  .filters label { color: #8b949e; font-size: 12px; }
  table { width: 100%; border-collapse: collapse; }
  thead { position: sticky; top: 0; background: #161b22; z-index: 10; }
  th { padding: 8px 12px; text-align: left; color: #8b949e; font-size: 11px; border-bottom: 1px solid #30363d; white-space: nowrap; }
  td { padding: 6px 12px; border-bottom: 1px solid #21262d; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 300px; }
  tr:hover td { background: #161b22; }
  .table-wrap { overflow: auto; height: calc(100vh - 230px); }
  .badge { display: inline-block; padding: 1px 8px; border-radius: 3px; font-size: 11px; }
  .badge-R { background: #1a4721; color: #3fb950; }
  .badge-S { background: #21262d; color: #8b949e; }
  .badge-D { background: #3d2b1a; color: #f0883e; }
  .badge-Z { background: #3d1a1a; color: #f85149; }
  .badge-T { background: #3d3a1a; color: #d29922; }
  .badge-I { background: #1c2128; color: #484f58; }
  .cpu-bar { display: inline-block; height: 8px; border-radius: 2px; margin-right: 6px; vertical-align: middle; background: #58a6ff; }
  .cpu-high { background: #f85149; }
  .cpu-med  { background: #f0883e; }
  .pid  { color: #8b949e; }
  .name { color: #58a6ff; }
  .cmd  { color: #6e7681; font-size: 11px; }
  .user-root  { color: #f85149; }
  .user-janet { color: #3fb950; }
</style>
</head>
<body>
<header>
  <h1>⚡ Linux Process Monitor</h1>
  <div class="status">WebSocket: <span id="ws-status">connecting...</span> &nbsp;|&nbsp; Updated: <span id="last-update">-</span></div>
</header>

<div class="stats">
  <div class="stat-box"><div class="label">TOTAL</div><div class="value" id="total">-</div></div>
  <div class="stat-box"><div class="label">RUNNING</div><div class="value" id="s-running" style="color:#3fb950">-</div></div>
  <div class="stat-box"><div class="label">SLEEPING</div><div class="value" id="s-sleeping" style="color:#8b949e">-</div></div>
  <div class="stat-box"><div class="label">WAITING</div><div class="value" id="s-waiting" style="color:#f0883e">-</div></div>
  <div class="stat-box"><div class="label">ZOMBIE</div><div class="value" id="s-zombie" style="color:#f85149">-</div></div>
  <div class="stat-box"><div class="label">IDLE</div><div class="value" id="s-idle" style="color:#484f58">-</div></div>
</div>

<div class="filters">
  <label>Search:</label>
  <input type="text" id="search" placeholder="name, user, pid, cmd...">
  <label>State:</label>
  <select id="state-filter">
    <option value="">All</option>
    <option value="R">Running</option>
    <option value="S">Sleeping</option>
    <option value="D">Waiting</option>
    <option value="Z">Zombie</option>
    <option value="T">Stopped</option>
    <option value="I">Idle</option>
  </select>
  <label>Min CPU%:</label>
  <input type="number" id="cpu-filter" placeholder="0" style="width:70px" min="0">
  <label>Sort:</label>
  <select id="sort-by">
    <option value="cpu">CPU</option>
    <option value="mem">Memory</option>
    <option value="pid">PID</option>
    <option value="name">Name</option>
  </select>
</div>

<div class="table-wrap">
<table>
  <thead>
    <tr>
      <th>PID</th>
      <th>NAME</th>
      <th>STATE</th>
      <th>CPU %</th>
      <th>MEMORY</th>
      <th>MEM %</th>
      <th>NICE</th>
      <th>THREADS</th>
      <th>USER</th>
      <th>PPID</th>
      <th>STARTED</th>
      <th>COMMAND</th>
    </tr>
  </thead>
  <tbody id="tbody"></tbody>
</table>
</div>

<script>
let all = []

function connect() {
  const ws = new WebSocket('ws://localhost:9090')

  ws.onopen = () => {
    document.getElementById('ws-status').textContent = 'connected'
    document.getElementById('ws-status').style.color = '#3fb950'
  }
  ws.onclose = () => {
    document.getElementById('ws-status').textContent = 'reconnecting...'
    document.getElementById('ws-status').style.color = '#f85149'
    setTimeout(connect, 2000)
  }
  ws.onmessage = (e) => {
    const data = JSON.parse(e.data)
    all = data.processes
    document.getElementById('total').textContent = data.total_processes
    document.getElementById('last-update').textContent = new Date(data.timestamp).toLocaleTimeString()
    const s = data.state_summary || {}
    document.getElementById('s-running').textContent  = s['Running'] || 0
    document.getElementById('s-sleeping').textContent = s['Sleeping'] || 0
    document.getElementById('s-waiting').textContent  = s['Waiting (Disk IO)'] || 0
    document.getElementById('s-zombie').textContent   = s['Zombie'] || 0
    document.getElementById('s-idle').textContent     = s['Idle'] || 0
    render()
  }
}

function render() {
  const search   = document.getElementById('search').value.toLowerCase()
  const state    = document.getElementById('state-filter').value
  const minCpu   = parseFloat(document.getElementById('cpu-filter').value) || 0
  const sortBy   = document.getElementById('sort-by').value

  let list = all.filter(p => {
    if (state && p.state_code !== state) return false
    if (p.cpu_percent < minCpu) return false
    if (search && !`${p.pid} ${p.name} ${p.user} ${p.cmd}`.toLowerCase().includes(search)) return false
    return true
  })

  list.sort((a, b) => {
    if (sortBy === 'cpu')  return b.cpu_percent - a.cpu_percent
    if (sortBy === 'mem')  return b.memory_kb - a.memory_kb
    if (sortBy === 'pid')  return a.pid - b.pid
    if (sortBy === 'name') return a.name.localeCompare(b.name)
    return 0
  })

  document.getElementById('tbody').innerHTML = list.map(p => {
    const w = Math.min(p.cpu_percent * 2, 60)
    const cc = p.cpu_percent > 50 ? 'cpu-high' : p.cpu_percent > 20 ? 'cpu-med' : ''
    const uc = p.user === 'root' ? 'user-root' : p.user === 'janet' ? 'user-janet' : ''
    const mem = (p.memory_kb / 1024).toFixed(1)
    return `<tr>
      <td class="pid">${p.pid}</td>
      <td class="name">${p.name}</td>
      <td><span class="badge badge-${p.state_code}">${p.state_code} ${p.state}</span></td>
      <td><span class="cpu-bar ${cc}" style="width:${w}px"></span>${p.cpu_percent}%</td>
      <td>${mem} MB</td>
      <td>${p.memory_percent}%</td>
      <td style="color:${p.nice<0?'#f85149':p.nice>0?'#8b949e':'#e6edf3'}">${p.nice}</td>
      <td>${p.threads}</td>
      <td class="${uc}">${p.user}</td>
      <td class="pid">${p.parent_pid}</td>
      <td>${p.started}</td>
      <td class="cmd">${p.cmd.substring(0,80)}</td>
    </tr>`
  }).join('')
}

document.getElementById('search').addEventListener('input', render)
document.getElementById('state-filter').addEventListener('change', render)
document.getElementById('cpu-filter').addEventListener('input', render)
document.getElementById('sort-by').addEventListener('change', render)

connect()
</script>
</body>
</html>"""

# ─────────────────────────────────────────────
# HTTP SERVER
# ─────────────────────────────────────────────
class HTTPHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ('/', '/ui'):
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.end_headers()
            self.wfile.write(HTML.encode())
        elif self.path == '/processes':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            with snapshot_lock:
                self.wfile.write(json.dumps(latest_snapshot, indent=2).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass

def run_http():
    server = HTTPServer(('0.0.0.0', 8080), HTTPHandler)
    print("UI → http://localhost:8080")
    server.serve_forever()

# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
async def main():
    threading.Thread(target=run_http, daemon=True).start()
    print("WebSocket → ws://localhost:9090")
    print(f"JSON file → {OUTPUT_FILE}")
    async with websockets.serve(websocket_handler, '0.0.0.0', 9090):
        await collector_loop()

if __name__ == '__main__':
    asyncio.run(main())
