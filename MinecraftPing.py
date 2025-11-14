import socket
import time
import struct
import json
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import threading
from typing import Optional, Tuple, List, Dict
from collections import deque
import os

# 延迟校准因子 - 如果需要调整延迟值，请修改这个常量
CALIBRATION_FACTOR = 1.0

class MinecraftProtocolPing:
    def __init__(self, host: str, port: int = 25565):
        self.host = host
        self.port = port

    def ping(self, timeout: int = 5) -> Tuple[bool, float, Optional[dict]]:
        """
        使用纯Minecraft协议进行服务器延迟检测
        """
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)

            # 开始计时
            start_time = time.time()

            # 1. 建立TCP连接
            sock.connect((self.host, self.port))
            connect_time = time.time()

            # 2. 发送握手包
            self._send_handshake(sock)

            # 3. 发送状态请求
            self._send_status_request(sock)

            # 4. 接收状态响应
            server_info = self._receive_response(sock)
            status_time = time.time()

            # 5. 发送ping包
            ping_time = time.time()
            self._send_ping(sock, int(ping_time * 1000))

            # 6. 接收pong响应
            self._receive_pong(sock)
            pong_time = time.time()

            sock.close()

            # 计算延迟 - 使用ping-pong往返时间作为主要延迟指标
            ping_pong_latency = (pong_time - ping_time) * 1000

            # 应用校准因子
            calibrated_latency = ping_pong_latency * CALIBRATION_FACTOR

            return True, round(calibrated_latency, 2), server_info

        except socket.timeout:
            return False, 0, None
        except Exception as e:
            print(f"Minecraft protocol ping error: {e}")
            return False, 0, None

    def _send_handshake(self, sock):
        """发送握手包"""
        # 包ID (握手)
        packet = b'\x00'
        # 协议版本 (764 = 1.20.1)
        packet += self._pack_varint(764)
        # 服务器地址
        packet += self._pack_string(self.host)
        # 服务器端口
        packet += struct.pack('>H', self.port)
        # 下一状态 (1 = 状态)
        packet += self._pack_varint(1)

        # 添加包长度前缀并发送
        packet_with_length = self._pack_varint(len(packet)) + packet
        sock.send(packet_with_length)

    def _send_status_request(self, sock):
        """发送状态请求包"""
        # 包ID (状态请求)
        packet = b'\x00'

        # 添加包长度前缀并发送
        packet_with_length = self._pack_varint(len(packet)) + packet
        sock.send(packet_with_length)

    def _send_ping(self, sock, payload):
        """发送ping包"""
        # 包ID (ping)
        packet = b'\x01'
        # 负载 (时间戳)
        packet += struct.pack('>Q', payload)

        # 添加包长度前缀并发送
        packet_with_length = self._pack_varint(len(packet)) + packet
        sock.send(packet_with_length)

    def _receive_pong(self, sock):
        """接收pong响应"""
        # 读取包长度
        length = self._unpack_varint(sock)

        # 读取包ID
        packet_id = self._unpack_varint(sock)
        if packet_id != 0x01:  # pong包ID
            raise Exception("Unexpected packet ID for pong")

        # 读取时间戳
        timestamp = struct.unpack('>Q', sock.recv(8))[0]
        return timestamp

    def _receive_response(self, sock) -> dict:
        """接收服务器状态响应"""
        # 读取包长度
        length = self._unpack_varint(sock)

        # 读取包ID
        packet_id = self._unpack_varint(sock)
        if packet_id != 0x00:
            raise Exception("Unexpected packet ID")

        # 读取JSON数据长度
        json_length = self._unpack_varint(sock)

        # 读取JSON数据
        json_data = b''
        while len(json_data) < json_length:
            chunk = sock.recv(json_length - len(json_data))
            if not chunk:
                raise Exception("Connection closed")
            json_data += chunk

        # 解析JSON
        return json.loads(json_data.decode('utf-8'))

    def _pack_varint(self, value: int) -> bytes:
        """打包VarInt"""
        result = b''
        while True:
            byte = value & 0x7F
            value >>= 7
            if value != 0:
                byte |= 0x80
            result += bytes([byte])
            if value == 0:
                break
        return result

    def _unpack_varint(self, sock) -> int:
        """解包VarInt"""
        result = 0
        for i in range(5):
            byte = ord(sock.recv(1))
            result |= (byte & 0x7F) << (7 * i)
            if not (byte & 0x80):
                break
        return result

    def _pack_string(self, string: str) -> bytes:
        """打包字符串"""
        encoded = string.encode('utf-8')
        return self._pack_varint(len(encoded)) + encoded

class ServerStats:
    """服务器统计信息"""
    def __init__(self):
        self.latencies = deque(maxlen=100)  # 保留最近100次延迟记录
        self.fail_count = 0
        self.success_count = 0
        self.min_latency = float('inf')
        self.max_latency = 0
        self.current_latency = 0
        self.last_version = "未知"
        self.last_players = "0/0"  # 在线人数/最大人数

    def add_result(self, success: bool, latency: float = 0, version: str = None, players: str = None):
        """添加一次检测结果"""
        if success:
            self.success_count += 1
            self.current_latency = latency
            self.latencies.append(latency)

            # 更新版本信息
            if version:
                self.last_version = version

            # 更新玩家信息
            if players:
                self.last_players = players

            # 更新最小和最大延迟
            if latency < self.min_latency:
                self.min_latency = latency
            if latency > self.max_latency:
                self.max_latency = latency
        else:
            self.fail_count += 1

    def get_average_latency(self) -> float:
        """计算平均延迟"""
        if not self.latencies:
            return 0
        return sum(self.latencies) / len(self.latencies)

    def get_stats_summary(self) -> Dict[str, str]:
        """获取统计摘要"""
        avg_latency = self.get_average_latency()

        return {
            "current": f"{self.current_latency:.1f}ms" if self.current_latency > 0 else "-",
            "min": f"{self.min_latency:.1f}ms" if self.min_latency != float('inf') else "-",
            "max": f"{self.max_latency:.1f}ms" if self.max_latency > 0 else "-",
            "average": f"{avg_latency:.1f}ms" if avg_latency > 0 else "-",
            "failures": str(self.fail_count),
            "successes": str(self.success_count),
            "version": self.last_version,
            "players": self.last_players
        }

class MinecraftPingGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Minecraft服务器延迟检测")
        self.root.geometry("680x550")

        # 服务器统计信息
        self.server_stats = {}

        # 持续检测相关
        self.continuous_testing = False
        self.testing_thread = None

        self.setup_ui()
        self.servers = []

    def setup_ui(self):
        # 控制面板
        control_frame = ttk.Frame(self.root, padding="10")
        control_frame.pack(fill=tk.X)

        # 服务器输入区域
        input_frame = ttk.Frame(control_frame)
        input_frame.pack(fill=tk.X, pady=5)

        ttk.Label(input_frame, text="服务器地址:").grid(row=0, column=0, sticky=tk.W)
        self.host_entry = ttk.Entry(input_frame, width=20)
        self.host_entry.grid(row=0, column=1, padx=5)

        ttk.Label(input_frame, text="端口:").grid(row=0, column=2, sticky=tk.W, padx=(20, 0))
        self.port_entry = ttk.Entry(input_frame, width=10)
        self.port_entry.insert(0, "25565")
        self.port_entry.grid(row=0, column=3, padx=5)

        ttk.Button(input_frame, text="添加服务器", command=self.add_server).grid(row=0, column=4, padx=10)
        ttk.Button(input_frame, text="单次检测", command=self.single_ping).grid(row=0, column=5, padx=5)
        ttk.Button(input_frame, text="清空列表", command=self.clear_servers).grid(row=0, column=6, padx=5)

        # 批量操作区域
        batch_frame = ttk.Frame(control_frame)
        batch_frame.pack(fill=tk.X, pady=5)

        ttk.Label(batch_frame, text="批量操作:").grid(row=0, column=0, sticky=tk.W)

        ttk.Button(batch_frame, text="导入服务器", command=self.import_servers).grid(row=0, column=1, padx=5)
        ttk.Button(batch_frame, text="导出服务器", command=self.export_servers).grid(row=0, column=2, padx=5)

        # 导入导出说明
        help_label = ttk.Label(batch_frame, text="格式: 每行一个服务器, 地址:端口 或 地址(默认端口25565)", foreground="gray")
        help_label.grid(row=1, column=0, columnspan=7, pady=2, sticky=tk.W)

        # 持续检测控制区域
        continuous_frame = ttk.Frame(control_frame)
        continuous_frame.pack(fill=tk.X, pady=5)

        ttk.Label(continuous_frame, text="持续检测:").grid(row=0, column=0, sticky=tk.W)

        ttk.Label(continuous_frame, text="间隔(秒):").grid(row=0, column=1, sticky=tk.W, padx=(20, 0))
        self.interval_entry = ttk.Entry(continuous_frame, width=8)
        self.interval_entry.insert(0, "1")
        self.interval_entry.grid(row=0, column=2, padx=5)

        ttk.Label(continuous_frame, text="持续时间(秒):").grid(row=0, column=3, sticky=tk.W, padx=(20, 0))
        self.duration_entry = ttk.Entry(continuous_frame, width=8)
        self.duration_entry.insert(0, "60")
        self.duration_entry.grid(row=0, column=4, padx=5)

        self.start_continuous_btn = ttk.Button(continuous_frame, text="开始持续检测", command=self.start_continuous_ping)
        self.start_continuous_btn.grid(row=0, column=5, padx=10)

        self.stop_continuous_btn = ttk.Button(continuous_frame, text="停止检测", command=self.stop_continuous_ping, state="disabled")
        self.stop_continuous_btn.grid(row=0, column=6, padx=5)

        # 服务器列表
        list_frame = ttk.Frame(self.root, padding="10")
        list_frame.pack(fill=tk.BOTH, expand=True)

        # 在线人数列
        columns = ("host", "port", "status", "current", "min", "max", "average", "players", "failures", "version")
        self.tree = ttk.Treeview(list_frame, columns=columns, show="headings")

        self.tree.heading("host", text="服务器地址")
        self.tree.heading("port", text="端口")
        self.tree.heading("status", text="状态")
        self.tree.heading("current", text="当前延迟")
        self.tree.heading("min", text="最小延迟")
        self.tree.heading("max", text="最大延迟")
        self.tree.heading("average", text="平均延迟")
        self.tree.heading("players", text="在线人数")
        self.tree.heading("failures", text="失败")
        self.tree.heading("version", text="版本")

        self.tree.column("host", width=70, anchor="center")
        self.tree.column("port", width=25, anchor="center")
        self.tree.column("status", width=20, anchor="center")
        self.tree.column("current", width=35, anchor="center")
        self.tree.column("min", width=35, anchor="center")
        self.tree.column("max", width=35, anchor="center")
        self.tree.column("average", width=35, anchor="center")
        self.tree.column("players", width=45, anchor="center")
        self.tree.column("failures", width=10, anchor="center")
        self.tree.column("version", width=100, anchor="center")

        scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)

        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # 右键菜单
        self.setup_context_menu()

        # 状态栏
        self.status_var = tk.StringVar()
        self.status_var.set(f"就绪")
        status_bar = ttk.Label(self.root, textvariable=self.status_var, relief=tk.SUNKEN)
        status_bar.pack(side=tk.BOTTOM, fill=tk.X)

    def setup_context_menu(self):
        self.context_menu = tk.Menu(self.root, tearoff=0)
        self.context_menu.add_command(label="删除选中", command=self.delete_selected)
        self.context_menu.add_command(label="重置统计", command=self.reset_stats)

        self.tree.bind("<Button-3>", self.show_context_menu)

    def show_context_menu(self, event):
        if not self.continuous_testing:  # 只有在非持续检测状态才显示右键菜单
            item = self.tree.identify_row(event.y)
            if item:
                self.tree.selection_set(item)
                self.context_menu.post(event.x_root, event.y_root)

    def delete_selected(self):
        for item in self.tree.selection():
            index = self.tree.index(item)
            if index < len(self.servers):
                # 从统计信息中移除
                server_key = self.servers[index]
                if server_key in self.server_stats:
                    del self.server_stats[server_key]

                # 从服务器列表中移除
                self.servers.pop(index)
            self.tree.delete(item)

    def reset_stats(self):
        """重置选中服务器的统计信息"""
        for item in self.tree.selection():
            index = self.tree.index(item)
            if index < len(self.servers):
                server_key = self.servers[index]
                self.server_stats[server_key] = ServerStats()

                # 更新显示
                self.update_server_display(index, "待检测", "-", "-", "-", "-", "-", "0", "-")

    def add_server(self):
        host = self.host_entry.get().strip()
        port = self.port_entry.get().strip()

        if not host:
            messagebox.showerror("错误", "请输入服务器地址")
            return

        try:
            port = int(port)
        except ValueError:
            messagebox.showerror("错误", "端口号必须是数字")
            return

        server_key = (host, port)
        if server_key not in self.servers:
            self.servers.append(server_key)
            # 初始化统计信息
            self.server_stats[server_key] = ServerStats()
            self.tree.insert("", tk.END, values=(host, port, "待检测", "-", "-", "-", "-", "-", "0", "-"))

        self.host_entry.delete(0, tk.END)

    def clear_servers(self):
        self.servers.clear()
        self.server_stats.clear()
        for item in self.tree.get_children():
            self.tree.delete(item)

    def import_servers(self):
        """从文件导入服务器列表"""
        if self.continuous_testing:
            messagebox.showwarning("警告", "请先停止持续检测")
            return

        file_path = filedialog.askopenfilename(
            title="选择服务器列表文件",
            filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")]
        )

        if not file_path:
            return

        try:
            with open(file_path, 'r', encoding='utf-8') as file:
                lines = file.readlines()

            added_count = 0
            skipped_count = 0

            for line in lines:
                line = line.strip()
                if not line or line.startswith('#'):  # 跳过空行和注释
                    continue

                # 解析服务器地址和端口
                if ':' in line:
                    parts = line.split(':')
                    host = parts[0].strip()
                    try:
                        port = int(parts[1].strip())
                    except ValueError:
                        port = 25565  # 默认端口
                else:
                    host = line.strip()
                    port = 25565  # 默认端口

                # 检查服务器是否已存在
                server_key = (host, port)
                if server_key not in self.servers:
                    self.servers.append(server_key)
                    self.server_stats[server_key] = ServerStats()
                    self.tree.insert("", tk.END, values=(host, port, "待检测", "-", "-", "-", "-", "-", "0", "-"))
                    added_count += 1
                else:
                    skipped_count += 1

            messagebox.showinfo("导入完成", f"成功导入 {added_count} 个服务器\n跳过 {skipped_count} 个重复服务器")

        except Exception as e:
            messagebox.showerror("导入错误", f"导入服务器列表时出错:\n{str(e)}")

    def export_servers(self):
        """导出服务器列表到文件"""
        if not self.servers:
            messagebox.showwarning("警告", "没有服务器可以导出")
            return

        file_path = filedialog.asksaveasfilename(
            title="保存服务器列表",
            defaultextension=".txt",
            filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")]
        )

        if not file_path:
            return

        try:
            with open(file_path, 'w', encoding='utf-8') as file:
                file.write("# Minecraft服务器列表\n")
                file.write("# 格式: 地址:端口 或 地址(默认端口25565)\n")
                file.write("# 生成时间: " + time.strftime("%Y-%m-%d %H:%M:%S") + "\n\n")

                for host, port in self.servers:
                    if port == 25565:
                        file.write(f"{host}\n")  # 使用默认端口时只写地址
                    else:
                        file.write(f"{host}:{port}\n")

            messagebox.showinfo("导出完成", f"服务器列表已导出到:\n{file_path}")

        except Exception as e:
            messagebox.showerror("导出错误", f"导出服务器列表时出错:\n{str(e)}")

    def single_ping(self):
        """单次检测所有服务器"""
        if self.continuous_testing:
            messagebox.showwarning("警告", "请先停止持续检测")
            return

        if not self.servers:
            messagebox.showwarning("警告", "没有要检测的服务器")
            return

        thread = threading.Thread(target=self.ping_all_servers)
        thread.daemon = True
        thread.start()

    def ping_all_servers(self):
        """检测所有服务器"""
        for i, (host, port) in enumerate(self.servers):
            # 只更新状态为"检测中..."，保留其他数据
            self.root.after(0, self.update_server_status_only, i, "检测中...")

            # 创建ping实例
            ping = MinecraftProtocolPing(host, port)

            success, latency, info = ping.ping()
            server_key = (host, port)

            # 获取版本信息和在线人数
            version = "未知"
            players = "0/0"
            if success and info:
                version = info.get('version', {}).get('name', '未知')

                # 获取在线人数信息
                players_data = info.get('players', {})
                online = players_data.get('online', 0)
                max_players = players_data.get('max', 0)
                players = f"{online}/{max_players}"

            # 更新统计信息
            self.server_stats[server_key].add_result(success, latency, version, players)
            stats = self.server_stats[server_key].get_stats_summary()

            if success:
                self.root.after(0, self.update_server_display, i, "在线", 
                              stats["current"], stats["min"], stats["max"], 
                              stats["average"], stats["players"], stats["failures"], stats["version"])
            else:
                self.root.after(0, self.update_server_display, i, "离线", 
                              stats["current"], stats["min"], stats["max"], 
                              stats["average"], stats["players"], stats["failures"], stats["version"])

        self.root.after(0, lambda: self.status_var.set("单次检测完成"))

    def start_continuous_ping(self):
        """开始持续检测"""
        if self.continuous_testing:
            return

        if not self.servers:
            messagebox.showwarning("警告", "没有要检测的服务器")
            return

        try:
            interval = float(self.interval_entry.get())
            duration = float(self.duration_entry.get())

            if interval <= 0 or duration <= 0:
                messagebox.showerror("错误", "间隔和持续时间必须大于0")
                return
        except ValueError:
            messagebox.showerror("错误", "请输入有效的数字")
            return

        # 更新UI状态
        self.continuous_testing = True
        self.start_continuous_btn.config(state="disabled")
        self.stop_continuous_btn.config(state="normal")

        # 启动持续检测线程
        self.testing_thread = threading.Thread(
            target=self.continuous_ping_worker, 
            args=(interval, duration)
        )
        self.testing_thread.daemon = True
        self.testing_thread.start()

    def stop_continuous_ping(self):
        """停止持续检测"""
        self.continuous_testing = False
        self.start_continuous_btn.config(state="normal")
        self.stop_continuous_btn.config(state="disabled")
        self.status_var.set("持续检测已停止")

    def continuous_ping_worker(self, interval: float, duration: float):
        """持续检测工作线程"""
        start_time = time.time()
        iteration = 0

        while self.continuous_testing:
            iteration += 1
            current_time = time.time()
            elapsed = current_time - start_time

            # 检查是否超过持续时间
            if duration > 0 and elapsed >= duration:
                self.root.after(0, self.stop_continuous_ping)
                self.root.after(0, lambda: self.status_var.set(f"持续检测已完成，共检测 {iteration} 次"))
                break

            # 更新状态
            remaining = duration - elapsed if duration > 0 else float('inf')
            self.root.after(0, lambda: self.status_var.set(
                f"持续检测中... 第 {iteration} 次检测, 已运行 {elapsed:.1f} 秒" + 
                (f", 剩余 {remaining:.1f} 秒" if duration > 0 else "")
            ))

            # 检测所有服务器
            for i, (host, port) in enumerate(self.servers):
                if not self.continuous_testing:
                    break

                # 只更新状态为"检测中..."，保留其他数据
                self.root.after(0, self.update_server_status_only, i, "检测中...")

                # 创建ping实例
                ping = MinecraftProtocolPing(host, port)

                success, latency, info = ping.ping()
                server_key = (host, port)

                # 获取版本信息和在线人数
                version = "未知"
                players = "0/0"
                if success and info:
                    version = info.get('version', {}).get('name', '未知')

                    # 获取在线人数信息
                    players_data = info.get('players', {})
                    online = players_data.get('online', 0)
                    max_players = players_data.get('max', 0)
                    players = f"{online}/{max_players}"

                # 更新统计信息
                self.server_stats[server_key].add_result(success, latency, version, players)
                stats = self.server_stats[server_key].get_stats_summary()

                if success:
                    self.root.after(0, self.update_server_display, i, "在线", 
                                  stats["current"], stats["min"], stats["max"], 
                                  stats["average"], stats["players"], stats["failures"], stats["version"])
                else:
                    self.root.after(0, self.update_server_display, i, "离线", 
                                  stats["current"], stats["min"], stats["max"], 
                                  stats["average"], stats["players"], stats["failures"], stats["version"])

            # 等待指定间隔
            if self.continuous_testing:
                time.sleep(interval)

    def update_server_status_only(self, index, status):
        """只更新服务器状态，保留其他数据"""
        items = self.tree.get_children()
        if index < len(items):
            item = items[index]
            current_values = self.tree.item(item, 'values')
            # 只更新状态，其他数据保持不变
            new_values = (
                current_values[0],  # host
                current_values[1],  # port
                status,             # status
                current_values[3],  # current
                current_values[4],  # min
                current_values[5],  # max
                current_values[6],  # average
                current_values[7],  # players
                current_values[8],  # failures
                current_values[9]   # version
            )
            self.tree.item(item, values=new_values)

    def update_server_display(self, index, status, current, min_latency, max_latency, average, players, failures, version):
        """更新服务器显示信息"""
        items = self.tree.get_children()
        if index < len(items):
            item = items[index]
            current_values = self.tree.item(item, 'values')
            # 保持主机和端口不变
            host, port = current_values[0], current_values[1]
            new_values = (host, port, status, current, min_latency, max_latency, average, players, failures, version)
            self.tree.item(item, values=new_values)

if __name__ == "__main__":
    # 启动GUI版本
    root = tk.Tk()
    app = MinecraftPingGUI(root)
    root.mainloop()
