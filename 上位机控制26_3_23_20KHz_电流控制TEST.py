import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import time
from datetime import datetime
import ctypes
import ctypes.wintypes

class CH341I2CController:
    def __init__(self):
        # 尝试加载CH341 DLL
        try:
            self.ch341_dll = ctypes.WinDLL("CH341DLLA64.dll")
            self.dll_version = "64-bit"
        except:
            try:
                self.ch341_dll = ctypes.WinDLL("CH341DLL.dll")
                self.dll_version = "32-bit"
            except Exception as e:
                print(f"无法加载CH341 DLL: {e}")
                self.ch341_dll = None
                self.dll_version = None
        
        self.device_index = 0
        self.device_handle = None
        self.is_connected = False
        self.i2c_speed = 1  # 默认100KHz
        
        if self.ch341_dll:
            self.init_dll_functions()
    
    def init_dll_functions(self):
        """初始化DLL函数"""
        self.CH341OpenDevice = self.ch341_dll.CH341OpenDevice
        self.CH341OpenDevice.argtypes = [ctypes.c_ulong]
        self.CH341OpenDevice.restype = ctypes.c_void_p
        
        self.CH341CloseDevice = self.ch341_dll.CH341CloseDevice
        self.CH341CloseDevice.argtypes = [ctypes.c_ulong]
        self.CH341CloseDevice.restype = None
        
        self.CH341SetStream = self.ch341_dll.CH341SetStream
        self.CH341SetStream.argtypes = [ctypes.c_ulong, ctypes.c_ulong]
        self.CH341SetStream.restype = ctypes.c_bool
        
        self.CH341StreamI2C = self.ch341_dll.CH341StreamI2C
        self.CH341StreamI2C.argtypes = [
            ctypes.c_ulong,
            ctypes.c_ulong,
            ctypes.c_void_p,
            ctypes.c_ulong,
            ctypes.c_void_p
        ]
        self.CH341StreamI2C.restype = ctypes.c_bool
        
        self.CH341GetVersion = self.ch341_dll.CH341GetVersion
        self.CH341GetVersion.argtypes = []
        self.CH341GetVersion.restype = ctypes.c_ulong
        
        self.CH341FlushBuffer = self.ch341_dll.CH341FlushBuffer
        self.CH341FlushBuffer.argtypes = [ctypes.c_ulong]
        self.CH341FlushBuffer.restype = ctypes.c_bool
        
    def connect_device(self, index=0):
        """连接CH341设备"""
        if not self.ch341_dll:
            return False, "未找到CH341 DLL库"
        
        try:
            self.device_handle = self.CH341OpenDevice(index)
            if self.device_handle:
                # 设置I2C模式
                mode = self.i2c_speed
                success = self.CH341SetStream(index, mode)
                if success:
                    self.device_index = index
                    self.is_connected = True
                    dll_version = self.CH341GetVersion()
                    version_info = {
                        'dll': dll_version,
                        'dll_type': self.dll_version
                    }
                    return True, version_info
                else:
                    self.CH341CloseDevice(index)
                    return False, "无法设置I2C模式"
            else:
                return False, "无法打开设备，请检查设备连接"
        except Exception as e:
            return False, f"连接设备时出错: {str(e)}"
    
    def disconnect_device(self):
        """断开CH341设备"""
        if self.is_connected and self.ch341_dll:
            self.CH341CloseDevice(self.device_index)
            self.is_connected = False
            self.device_handle = None
        return True
    
    def set_i2c_speed(self, speed):
        """设置I2C速度"""
        if speed in [0, 1, 2, 3]:
            self.i2c_speed = speed
            if self.is_connected and self.ch341_dll:
                return self.CH341SetStream(self.device_index, speed)
        return False
    
    def flush_buffer(self):
        """清空缓冲区"""
        if self.is_connected and self.ch341_dll:
            return self.CH341FlushBuffer(self.device_index)
        return False
    
    def i2c_write(self, device_addr_7bit, write_data):
        """新增：I2C纯写操作 (不等待读回)"""
        if not self.is_connected or not self.ch341_dll:
            return False, "设备未连接"
            
        try:
            if not self.CH341SetStream(self.device_index, self.i2c_speed):
                return False, "设置I2C速度失败"
                
            self.flush_buffer()
            
            # 写操作
            write_addr_8bit = (device_addr_7bit << 1) & 0xFE
            write_buffer = (ctypes.c_ubyte * (len(write_data) + 1))()
            write_buffer[0] = write_addr_8bit
            
            for i, byte in enumerate(write_data):
                write_buffer[i + 1] = byte
                
            success = self.CH341StreamI2C(
                self.device_index,
                len(write_buffer),
                ctypes.byref(write_buffer),
                0,
                None
            )
            
            if success:
                return True, "写入成功"
            else:
                return False, "写入失败"
        except Exception as e:
            return False, f"写操作出错: {str(e)}"

    def i2c_write_read(self, device_addr_7bit, write_data, read_length):
        """I2C先写后读操作"""
        if not self.is_connected or not self.ch341_dll:
            return False, "设备未连接"
        
        try:
            if not self.CH341SetStream(self.device_index, self.i2c_speed):
                return False, "设置I2C速度失败"
            
            self.flush_buffer()
            
            # 写操作
            write_addr_8bit = (device_addr_7bit << 1) & 0xFE
            write_buffer = (ctypes.c_ubyte * (len(write_data) + 1))()
            write_buffer[0] = write_addr_8bit
            
            for i, byte in enumerate(write_data):
                write_buffer[i + 1] = byte
            
            success1 = self.CH341StreamI2C(
                self.device_index,
                len(write_buffer),
                ctypes.byref(write_buffer),
                0,
                None
            )
            
            if not success1:
                return False, "写入失败"
            
            time.sleep(0.001)  # 1ms延迟
            
            # 读操作
            read_addr_8bit = (device_addr_7bit << 1) | 0x01
            read_addr_buffer = (ctypes.c_ubyte * 1)()
            read_addr_buffer[0] = read_addr_8bit
            read_buffer = (ctypes.c_ubyte * read_length)()
            
            success2 = self.CH341StreamI2C(
                self.device_index,
                1,
                ctypes.byref(read_addr_buffer),
                read_length,
                ctypes.byref(read_buffer)
            )
            
            if success2:
                data = [read_buffer[i] for i in range(read_length)]
                return True, data
            else:
                return False, "读取失败"
        except Exception as e:
            return False, f"写读操作出错: {str(e)}"

class SimpleI2CReaderApp:
    def __init__(self, root):
        self.root = root
        self.root.title("CH341 I2C 读数工具")
        self.root.minsize(680, 580)
        
        self.ch341_controller = CH341I2CController()
        
        self.setup_gui()
        
    def setup_gui(self):
        # 设置全局样式和字体
        style = ttk.Style()
        if 'clam' in style.theme_names():
            style.theme_use('clam')
            
        default_font = ("Microsoft YaHei", 10)
        self.root.option_add("*Font", default_font)
        style.configure(".", font=default_font)
        style.configure("TLabelframe.Label", font=("Microsoft YaHei", 10, "bold"), foreground="#333333")
        style.configure("TButton", padding=4)
        style.configure("Connect.TButton", font=("Microsoft YaHei", 10, "bold"))

        main_frame = ttk.Frame(self.root, padding="15")
        main_frame.pack(fill="both", expand=True)
        
        # ============ 上位机控制区 ============
        i2c_frame = ttk.LabelFrame(main_frame, text="CH341 I2C 设备控制", padding=15)
        i2c_frame.pack(fill="x", pady=(0, 10))
        
        ttk.Label(i2c_frame, text="I2C设备地址(7位):").grid(row=0, column=0, sticky="w", pady=5)
        self.i2c_addr_var = tk.StringVar(value="0x41")
        self.i2c_addr_entry = ttk.Entry(i2c_frame, textvariable=self.i2c_addr_var, width=12)
        self.i2c_addr_entry.grid(row=0, column=1, sticky="w", padx=5, pady=5)
        self.i2c_addr_entry.bind('<KeyRelease>', lambda e: self.update_address_info())
        
        self.addr_info_label = ttk.Label(i2c_frame, text="写地址: 0x82, 读地址: 0x83", foreground="#0066cc")
        self.addr_info_label.grid(row=0, column=2, sticky="w", padx=10, pady=5)
        
        ttk.Label(i2c_frame, text="I2C通讯速度:").grid(row=1, column=0, sticky="w", pady=5)
        self.i2c_speed_var = tk.StringVar(value="低速 (20KHz)")
        speed_combo = ttk.Combobox(i2c_frame, textvariable=self.i2c_speed_var,
                                  values=["低速 (20KHz)", "标准 (100KHz)", "快速 (400KHz)", "高速 (750KHz)"],
                                  width=15, state="readonly")
        speed_combo.grid(row=1, column=1, sticky="w", padx=5, pady=5)
        
        self.i2c_connect_btn = ttk.Button(i2c_frame, text="连接 CH341", command=self.connect_i2c_device, style="Connect.TButton")
        self.i2c_connect_btn.grid(row=2, column=0, columnspan=2, sticky="we", pady=(10, 0))
        
        status_frame = ttk.Frame(i2c_frame)
        status_frame.grid(row=2, column=2, sticky="w", padx=10, pady=(10, 0))
        ttk.Label(status_frame, text="当前状态:").pack(side="left")
        self.i2c_status_label = ttk.Label(status_frame, text="未连接", foreground="#e63946", font=("Microsoft YaHei", 10, "bold"))
        self.i2c_status_label.pack(side="left", padx=5)

        # 把读写区横向排列
        cmd_container = ttk.Frame(main_frame)
        cmd_container.pack(fill="x", pady=(0, 10))
        cmd_container.columnconfigure(0, weight=1)
        cmd_container.columnconfigure(1, weight=1)

        # ============ 状态回读区 ============
        read_frame = ttk.LabelFrame(cmd_container, text="状态回读", padding=15)
        read_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        
        ttk.Label(read_frame, text="指令: B4 88 00 00").grid(row=0, column=0, sticky="w", pady=5)
        ttk.Button(read_frame, text="回读输入电压", command=self.read_input_voltage).grid(row=0, column=1, padx=10, pady=5)
        
        ttk.Label(read_frame, text="指令: B4 8B 00 00").grid(row=1, column=0, sticky="w", pady=5)
        ttk.Button(read_frame, text="回读输出电压", command=self.read_output_voltage).grid(row=1, column=1, padx=10, pady=5)
        
        ttk.Label(read_frame, text="指令: B4 8C 00 00").grid(row=2, column=0, sticky="w", pady=5)
        ttk.Button(read_frame, text="回读输出电流", command=self.read_output_current).grid(row=2, column=1, padx=10, pady=5)

        # ============ 参数设置区 ============
        set_frame = ttk.LabelFrame(cmd_container, text="参数设置", padding=15)
        set_frame.grid(row=0, column=1, sticky="nsew", padx=(5, 0))
        
        ttk.Label(set_frame, text="设置输出电流 (0-20A):").grid(row=0, column=0, sticky="w", pady=5)
        
        current_input_frame = ttk.Frame(set_frame)
        current_input_frame.grid(row=0, column=1, sticky="w", padx=5, pady=5)
        
        self.set_current_var = tk.StringVar(value="0")
        current_values = [str(i) for i in range(21)]
        self.current_combo = ttk.Combobox(current_input_frame, textvariable=self.set_current_var, 
                                          values=current_values, width=6, state="readonly")
        self.current_combo.pack(side="left")
        ttk.Label(current_input_frame, text=" A").pack(side="left", padx=(2, 0))
        
        ttk.Button(set_frame, text="确认下发", command=self.set_output_current).grid(row=1, column=0, columnspan=2, sticky="we", pady=15)

        # ============ 输出日志区 ============
        log_frame = ttk.LabelFrame(main_frame, text="操作日志", padding=10)
        log_frame.pack(fill="both", expand=True)
        
        btn_clear = ttk.Button(log_frame, text="清空日志", command=self.clear_log_info)
        btn_clear.pack(anchor="ne", pady=(0, 5))
        
        self.log_info = scrolledtext.ScrolledText(log_frame, height=12, font=("Consolas", 10))
        self.log_info.pack(fill="both", expand=True)
        
        self.update_address_info()

    def update_address_info(self, event=None):
        try:
            addr_str = self.i2c_addr_var.get().strip()
            if addr_str.startswith("0x") or addr_str.startswith("0X"):
                addr_str = addr_str[2:]
            
            if not addr_str:
                self.addr_info_label.config(text="请输入7位地址", foreground="#e63946")
                return
            
            device_addr_7bit = int(addr_str, 16)
            if device_addr_7bit > 0x7F:
                self.addr_info_label.config(text="错误: 地址必须小于0x80", foreground="#e63946")
                return
            
            write_addr_8bit = (device_addr_7bit << 1) & 0xFE
            read_addr_8bit = (device_addr_7bit << 1) | 0x01
            self.addr_info_label.config(
                text=f"写地址: 0x{write_addr_8bit:02X}, 读地址: 0x{read_addr_8bit:02X}",
                foreground="#0066cc"
            )
        except ValueError:
            self.addr_info_label.config(text="地址格式错误", foreground="#e63946")
            
    def connect_i2c_device(self):
        if not self.ch341_controller.is_connected:
            try:
                success, result = self.ch341_controller.connect_device(0)
                if success:
                    speed_map = {"低速 (20KHz)": 0, "标准 (100KHz)": 1, "快速 (400KHz)": 2, "高速 (750KHz)": 3}
                    speed_text = self.i2c_speed_var.get()
                    if speed_text in speed_map:
                        self.ch341_controller.set_i2c_speed(speed_map[speed_text])
                    
                    self.i2c_connect_btn.config(text="断开 CH341")
                    self.i2c_status_label.config(text="已连接", foreground="#2a9d8f")
                    self.add_log_info("CH341 I2C设备连接成功")
                else:
                    messagebox.showerror("连接失败", result)
            except Exception as e:
                messagebox.showerror("连接错误", str(e))
        else:
            self.ch341_controller.disconnect_device()
            self.i2c_connect_btn.config(text="连接 CH341")
            self.i2c_status_label.config(text="未连接", foreground="#e63946")
            self.add_log_info("CH341 I2C设备已断开")

    def parse_hex_string(self, hex_str):
        try:
            parts = hex_str.strip().split()
            data = []
            for part in parts:
                if part.startswith("0x") or part.startswith("0X"):
                    part = part[2:]
                if not part:
                    continue
                data.append(int(part, 16))
            return data
        except ValueError:
            raise ValueError("无效的十六进制数据格式")

    def decode_i2c_value(self, data):
        """解码规则: 第3字节为整数, 第4字节为小数(除以100)"""
        if len(data) >= 4:
            command_echo = data[0:2]
            integer_part = data[2]
            decimal_part = data[3] / 100.0
            final_value = integer_part + decimal_part
            return {
                "command_echo": command_echo,
                "integer_part": integer_part,
                "decimal_part": decimal_part,
                "final_value": final_value
            }
        return None

    def format_raw_data(self, data):
        if not data:
            return "无数据"
        return " ".join([f"0x{b:02X}" for b in data])

    def get_i2c_address(self):
        try:
            addr_str = self.i2c_addr_var.get().strip()
            if addr_str.startswith("0x") or addr_str.startswith("0X"):
                addr_str = addr_str[2:]
            if not addr_str: return 0x41
            return int(addr_str, 16)
        except:
            return 0x41

    # ============ 核心读写逻辑 ============
    def execute_read_command(self, cmd_str, param_name, unit):
        if not self.ch341_controller.is_connected:
            messagebox.showwarning("警告", "请先连接CH341 I2C设备")
            return

        try:
            device_addr_7bit = self.get_i2c_address()
            write_data = self.parse_hex_string(cmd_str)
            
            success, result = self.ch341_controller.i2c_write_read(device_addr_7bit, write_data, 4)
            
            if success:
                decoded = self.decode_i2c_value(result)
                if decoded:
                    val = decoded["final_value"]
                    raw_str = self.format_raw_data(result)
                    self.add_log_info(f"成功 | {param_name}: {val:.2f} {unit}  (原始回读: {raw_str})")
                else:
                    self.add_log_info(f"错误 | 无法解码 {param_name} 数据 (原始回读: {self.format_raw_data(result)})")
            else:
                self.add_log_info(f"失败 | 读取 {param_name} 指令发送失败")
        except Exception as e:
            self.add_log_info(f"异常 | 执行 {param_name} 时出错: {str(e)}")

    def read_input_voltage(self):
        self.execute_read_command("B4 88 00 00", "输入电压", "V")

    def read_output_voltage(self):
        self.execute_read_command("B4 8B 00 00", "输出电压", "V")

    def read_output_current(self):
        self.execute_read_command("B4 8C 00 00", "输出电流", "A")
        
    def set_output_current(self):
        """新增：执行设置电流的逻辑"""
        if not self.ch341_controller.is_connected:
            messagebox.showwarning("警告", "请先连接CH341 I2C设备")
            return

        try:
            # 获取用户选择的电流值并转换为整数
            current_val = int(self.set_current_var.get())
            if current_val < 0 or current_val > 20:
                self.add_log_info("错误 | 电流设置值超出范围 (0-20A)")
                return
            
            device_addr_7bit = self.get_i2c_address()
            
            # 构造指令: 0xB4 0xFF 0xXX 0x00，其中XX是电流值的十六进制
            write_data = [0xB4, 0xFF, current_val, 0x00]
            
            # 下发指令 (不强制读回数据)
            success, result = self.ch341_controller.i2c_write(device_addr_7bit, write_data)
            
            hex_str = " ".join([f"{b:02X}" for b in write_data])
            if success:
                self.add_log_info(f"成功 | 设置输出电流: {current_val}A (发送数据: {hex_str})")
            else:
                self.add_log_info(f"失败 | 设置输出电流指令发送失败 (发送数据: {hex_str})")
        except Exception as e:
            self.add_log_info(f"异常 | 执行设置电流时出错: {str(e)}")

    # ============ 日志管理 ============
    def add_log_info(self, message):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_info.insert(tk.END, f"[{timestamp}] {message}\n")
        self.log_info.see(tk.END)
    
    def clear_log_info(self):
        self.log_info.delete(1.0, tk.END)
        
    def on_closing(self):
        if self.ch341_controller.is_connected:
            self.ch341_controller.disconnect_device()
        self.root.destroy()

def main():
    root = tk.Tk()
    app = SimpleI2CReaderApp(root)
    root.protocol("WM_DELETE_WINDOW", app.on_closing)
    root.mainloop()

if __name__ == "__main__":
    main()