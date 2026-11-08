"""Rebuild the customer guide from the current UI screenshots. No device access."""
from pathlib import Path
import json
from xml.sax.saxutils import escape

from PIL import Image
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import Paragraph

ROOT = Path(__file__).resolve().parents[3]
SHOTS = ROOT / 'output/playwright/customer-guide'
OUT = Path(__file__).parent
PDF = OUT / 'Power_Test_客户操作手册_图解版.pdf'
pdfmetrics.registerFont(TTFont('CN', '/System/Library/Fonts/STHeiti Light.ttc', subfontIndex=0))
pdfmetrics.registerFont(TTFont('CN-Bold', '/System/Library/Fonts/STHeiti Medium.ttc', subfontIndex=0))
pdfmetrics.registerFontFamily('CN', normal='CN', bold='CN-Bold')
W, H = landscape(A4)
INK = colors.HexColor('#192532')
MUTED = colors.HexColor('#566574')
ACCENT = colors.HexColor('#B9540A')
LINE = colors.HexColor('#D8DEE5')
PALE = colors.HexColor('#F5F7FA')
WARN = colors.HexColor('#FFF4E6')
c = canvas.Canvas(str(PDF), pagesize=(W, H), pageCompression=1)
c.setTitle('Power Test 客户操作手册 · 图解版')
c.setAuthor('Power Test')
c.setSubject('Windows 10/11；安装、授权、设备设置、自动测试、结果查看及常见问题')
audit = []

def text(s, x, top, width, size=11, leading=None, bold=False, color=INK):
    style = ParagraphStyle('p', fontName='CN-Bold' if bold else 'CN', fontSize=size,
                           leading=leading or size*1.55, textColor=color, wordWrap='CJK')
    p = Paragraph(escape(s).replace('\n', '<br/>'), style)
    _, h = p.wrap(width, H)
    if top-h < 22:
        raise ValueError(f'Text overflow on page {c.getPageNumber()}: {s[:45]}')
    p.drawOn(c, x, top-h)
    return top-h

def footer():
    c.setStrokeColor(LINE); c.setLineWidth(.5); c.line(28, 28, W-28, 28)
    c.setFont('CN', 8); c.setFillColor(MUTED)
    c.drawString(28, 12, 'Power Test 0.1.2  ·  操作手册  ·  2026-09-28')
    c.drawRightString(W-28, 12, f'{c.getPageNumber():02d}')

def begin(title, subtitle='', section='操作图解'):
    c.setFillColor(colors.white); c.rect(0, 0, W, H, fill=1, stroke=0)
    c.bookmarkPage(f'p{c.getPageNumber()}')
    c.addOutlineEntry(title, f'p{c.getPageNumber()}', level=0, closed=False)
    text(section, 28, H-18, W-56, 9, bold=True, color=ACCENT)
    text(title, 28, H-38, W-56, 22, bold=True, leading=27)
    if subtitle: text(subtitle, 28, H-71, W-56, 10, leading=14, color=MUTED)

def finish(title, image=None):
    audit.append({'page': c.getPageNumber(), 'title': title, 'screenshot': image})
    footer(); c.showPage()

def picture(name, box, crop=None):
    p = SHOTS/name
    im = Image.open(p)
    iw, ih = im.size
    cx, cy, cw, ch = crop or (0,0,iw,ih)
    if not (0 <= cx < iw and 0 <= cy < ih and cx+cw <= iw and cy+ch <= ih):
        raise ValueError((name, im.size, crop))
    x,y,bw,bh = box
    scale = min(bw/cw,bh/ch)
    rw,rh = cw*scale,ch*scale
    rx,ry = x+(bw-rw)/2,y+(bh-rh)/2
    c.saveState()
    clip=c.beginPath();clip.rect(rx,ry,rw,rh);c.clipPath(clip,stroke=0)
    c.drawImage(str(p),rx-cx*scale,ry-(ih-cy-ch)*scale,width=iw*scale,height=ih*scale,mask='auto')
    c.restoreState()
    c.setStrokeColor(LINE);c.setLineWidth(.5);c.rect(rx,ry,rw,rh,stroke=1,fill=0)
    return (rx,ry,rw,rh)

def steps(items, top, x=28, width=None, size=11.2, columns=3):
    width=width or W-56
    gap=20
    colw=(width-gap*(columns-1))/columns
    if columns==1:
        for i,(heading,body) in enumerate(items,1):
            top=text(f'{i:02d}  {heading}',x,top,colw,size+1,bold=True)
            top=text(body,x,top-5,colw,size)-15
        return top
    for i,(heading,body) in enumerate(items):
        xx=x+i*(colw+gap)
        t=text(f'{i+1:02d}  {heading}',xx,top,colw,size,bold=True)
        text(body,xx,t-4,colw,size-0.4,leading=14.6)

def notice(s, y=39):
    c.setFillColor(WARN);c.roundRect(28,y,W-56,34,4,fill=1,stroke=0)
    text(s,38,y+27,W-76,9.4,leading=12.6,color=colors.HexColor('#804108'))

def page(title, subtitle, shot, items, note='', crop=None, tall=False, caption='当前界面实拍 · 演示数据'):
    begin(title,subtitle)
    if tall:
        picture(shot,(28,88,390,H-185),crop)
        text(caption,28,80,390,8,color=MUTED)
        steps(items,H-118,x=442,width=W-470,size=12,columns=1)
    else:
        picture(shot,(28,190,W-56,H-277),crop)
        text(caption,28,182,W-56,8,color=MUTED)
        steps(items,165,size=10.8)
    if note: notice(note)
    finish(title,shot)

# Cover: a real current screenshot, with provenance and limits visible.
begin('Power Test 客户操作手册','Windows 10 / 11  ·  从首次安装到完成一次测试','图解版  /  2026.09')
picture('14-complete.png',(28,196,W-56,292),crop=(180,88,1248,540))
text('按照按钮名称操作，每一步都核对结果',28,178,W-56,17,bold=True)
text('适用版本：0.1.2。正文截图来自当前软件界面的实际操作，采用浅色主题；仪器身份、曲线和读数为演示数据。截图里的电流、增益和合格阈值均为示例，不是推荐测试参数。',28,148,W-56,11.5)
notice('截图环境为 macOS 浏览器预览。Windows 安装器、厂商驱动安装过程及实机测量未在本册中实拍。',y=45)
finish('封面','14-complete.png')

begin('先找你要做的事','首次使用按顺序阅读；设备已经配置好时，可从“开始一次自动测试”继续。','阅读导航')
toc=[('首次启用','安装与驱动 03  /  激活许可证 04  /  认识主界面 05'),
     ('准备测试','样品与目录 06  /  TDK 电源 07-08  /  CH341 电源 09'),
     ('确认测量','功率计 10-12  /  光谱仪 13-14  /  脚本与规格 15'),
     ('执行与收尾','开始与监控 16-17  /  结束与结果 18-19  /  停止和急停 20'),
     ('查数据与扩展功能','数据浏览 21-23  /  比较记录 24-25  /  手动测试 26'),
     ('按需使用','PD 采集 27  /  出货报告 28-29  /  外观 30  /  常见问题 31')]
y=H-115
for head,body in toc:
    y=text(head,34,y,170,13,bold=True)
    text(body,209,y+20,W-243,11.5)
    y-=36
text('使用前核对',34,178,W-68,14,bold=True)
text('确认器件接线、冷却、光路和电源限值符合现场测试规程。先确定样品允许的电流、电压与光功率范围，再填写软件参数。',34,151,W-68,12)
notice('完成测试流程、指标合格、文件保存成功是三件不同的事；每次测试结束都要分别确认。')
finish('阅读导航')

begin('01  安装软件与对应驱动','仅首次安装或更换设备时需要。当前安装包不会自动安装仪器驱动。','首次启用')
text('先运行交付的 Power Test 安装程序；再按下表安装实际使用的驱动。驱动已可正常工作时，不必重复安装。',28,H-109,W-56,11.5)
rows=[
('软件无法启动 / 缺少 WebView2','安装交付包中的 Microsoft WebView2 Runtime。'),
('TDK 串口电源、VISA 串口功率计','安装 NI-VISA；使用 USB 转串口线时，安装对应芯片驱动。'),
('CH341 I²C 控制器','安装 CH341PAR / CH341WDM 对应驱动；与 CH341 串口驱动不同。'),
('Ocean Insight 光谱仪','安装型号对应的 Ocean 驱动；随包 HR4 驱动仅用于对应型号。'),
('Ophir USB 功率计','运行 StarLab_Setup.exe；先确认 StarLab 能识别仪器。'),
('Coherent PowerMax / LaserPoint','按型号安装 Coherent Meter Connection 或对应 FTDI 驱动。'),
('NI-DAQ / PD 采集卡','安装对应 NI-DAQmx 运行环境。仅进行其他测试时无需为此安装。')]
y=H-157
for i,(a,b) in enumerate(rows):
    h=40
    c.setFillColor(PALE if i%2==0 else colors.white);c.rect(28,y-h,W-56,h,fill=1,stroke=0)
    text(a,37,y-7,248,10.4,bold=True,leading=13.5)
    text(b,298,y-7,W-338,10.5,leading=13.5)
    y-=h
text('安装完成后',28,y-12,120,11,bold=True)
text('按安装器提示重启。连接设备后启动 Power Test，再使用软件内的“识别”。完整驱动包附带的目录以交付包内 DRIVER-INSTALLATION.txt 为准。',160,y-12,W-188,10.5)
notice('本页为 Windows 操作说明，无安装器截图。若厂商工具也识别不到设备，先处理驱动或连接问题。')
finish('01 安装软件与对应驱动')

page('02  激活当前电脑','首次启动在 Power Test 桌面应用中完成授权。','00-activation.png',[
('复制机器码','在桌面应用中点击“复制机器码”，把完整的 ARP-… 机器码提供给软件交付方。'),
('取得许可证','收到交付方针对这台电脑签发的 license.json。不要使用其他电脑的许可证。'),
('导入并进入软件','点击“导入许可证”，选中文件；验证通过后进入主界面。')],
note='本图是激活页预览，按钮不可用；Windows 桌面端会显示实际机器码并启用操作。',tall=True,
caption='当前激活组件实拍 · 浏览器预览，不是 Windows 激活成功截图')

page('03  认识主界面','日常测试以“自动测试”为入口，三张设备卡片可以直接点击。','11-ready.png',[
('左侧切换功能','自动测试用于按脚本测量；手动配置用于单独控制和单点记录；数据浏览用于查结果。'),
('点击设备卡片','电源、功率计、光谱仪分别打开自己的设置窗口。窗口里的“保存设置”保存当前配置。'),
('看顶部状态','运行时顶部显示测试阶段和点数；右上角提供“急停”。下方曲线用于观察实时读数。')],crop=(0,0,1440,640),
note='本手册用浅色主题截图。深色主题的按钮名称和操作路径相同。')

page('04  填写样品和保存位置','入口：自动测试页面顶部。每次换样品都重新核对 SN。','11-ready.png',[
('填写样品信息','输入产品 SN、测试温度和测试站别；使用实际追溯信息，不使用截图里的 DEMO-001。'),
('选择输出目录','点击文件夹按钮选择可写目录，也可输入路径。例如 D:\\PowerTest\\Results。'),
('选择测试脚本','首次常规扫描可选择已确认的“标准测试”，按下一页检查设备；修改配置后点击“保存”。')],crop=(184,97,1243,212),
note='保存位置使用客户电脑上的实际目录。不要把示例路径当成已经存在的结果文件。')

page('05  选择 TDK 电源和串口','使用 TDK 时按本页操作；使用 CH341 时跳到第 09 页。','02-power-select.png',[
('打开电源设置','点击电源卡片，在“控制器”中选择“TDK RS232”。'),
('识别并选串口','点击“识别”，展开“TDK 串口”，选择实际连接的资源。多台设备时逐一核对。'),
('建立连接','点击“连接 TDK”。连接后确认电源在线；连接动作本身不会自动开启输出。')],
note='图中串口名称为演示项；现场按实际资源选择。控制器选项锁定时，先安全断开已有连接。')

page('06  核对 TDK 输出和测试电流','电源连接成功后，先检查设定值，再决定是否开启输出。','03-power-connected.png',[
('核对电压与当前设定','按器件和供电方案确认输出电压、设定电流。界面里的“设置电压”“设置电流”会发送设备命令。'),
('核对扫描参数','检查起始电流、目标电流、电流间隔，以及单点超时、下电步长和下电间隔。'),
('保存并准备输出','点击“保存设置”。确认接线、负载和限值后，才执行“开启输出”；自动测试前确认输出状态。')],
note='截图中的 12 V、8 A、2-12 A 等数值仅作界面示例；不得照搬到未知器件。')

page('07  使用 CH341 电源控制器','这是 TDK 的另一条设备配置路径；只按实际使用的控制器选择。','04-ch341.png',[
('选择控制器','先保证已有电源安全断开，再选择“CH341 I²C”。'),
('识别物理设备','点击“识别”，在“CH341 设备”中选实际端口；列表为空时先检查驱动和 USB 连接。'),
('连接并保存','设备可用后点击“连接电源”，核对电流计划并“保存设置”。需要时读取电压、电流或温度。')],
note='本图展示未选择设备时的界面，因此连接按钮不可用。CH341 I²C 与 USB 串口不是同一种驱动。')

page('08  识别并选择功率计','入口：自动测试 → 功率计卡片。','05-meter-select.png',[
('扫描资源','确认设备连接到电脑，点击“识别”，等待完成后展开“串口资源”。'),
('按身份选仪器','核对品牌、资源及探头 SN。Ophir USB 也在此列表中，以 OPHIR::USB::… 资源显示。'),
('检查系数方案','选中仪器后核对探头 SN、当前方案、功率系数和校准波长，再保存设置。')],tall=True,
note='功率系数按探头 / 传感头 SN 绑定。更换探头后重新检查方案，不能只看电脑上的串口号。')

page('09  确认或新建系数方案','只有完成标定或获得已确认参数后，才修改测量系数。','06-meter-profile.png',[
('优先选已有方案','在“当前方案”中选择与探头、波长和光路条件匹配的方案，核对版本及备注。'),
('需要时新建','点击“新建”或“另存为”，填写方案名称、功率系数、校准波长和条件备注。'),
('保存方案','点击“保存方案”，再核对当前方案及版本。普通“保存设置”不会替代方案保存。')],tall=True,
note='采集中或存在未保存手动测试点时不能变更方案。未知校准波长的旧方案须先补全。')

page('10  开始功率采集与调零','识别到仪器后，还需要启动采集，才能获得实时功率。','07-meter-acquire.png',[
('启动采集','点击“开始采集”，确认按钮变为“停止采集”，并持续出现有效读数。'),
('需要时相对调零','只有在确认探头没有被测信号、读数稳定且流程要求调零时使用“相对调零”。'),
('保存并返回','确认方案正确后点击“保存设置”。“功率曲线平滑”仅改变显示，不改变测量数据。')],tall=True,
note='按钮显示“停止采集”只说明采集已启动；是否有效，还要核对实际读数和设备状态。')

page('11  选择光谱仪并设置采集参数','入口：自动测试 → 光谱仪卡片。需要记录光谱时配置。','08-spectrometer.png',[
('识别目标仪器','点击“识别”，展开“设备资源”；有多台时按光谱仪 SN 选目标设备。'),
('设置采集参数','根据实际光信号设置积分时间、刷新间隔、平均帧数；需要时启用“自动积分”。'),
('确认 PIB 范围','按测试要求填写 PIB 中心波长和半带宽，保存后再开始采集。')],tall=True,
note='演示列表显示“自动选择第一台 Ocean Insight”。实际多设备场景应确认对应 SN，不能把设备 ID 当固定身份。')

page('12  启动光谱采集并检查饱和','需要光谱结果时，在正式开始自动测试前检查曲线质量。','09-spectrum-acquire.png',[
('启动并观察','点击“开始采集”，确认按钮变为“停止采集”；回主界面检查光谱曲线和指标。'),
('处理饱和','出现饱和警告时，按现场流程降低积分时间或调整光路；重新测量，直到没有饱和提示。'),
('保存参数','确认采集参数后点击“保存设置”。后续产品改变光路或功率范围时，重新检查。')],tall=True,
note='光谱饱和时测试点不会作为有效结果正常记录。图上的峰值高度是仪器响应值，不是绝对光功率。')

page('13  编辑测试脚本和合格规格','入口：自动测试 → 脚本选择旁的“编辑”。','10-test-settings.png',[
('确认电流计划','按已批准的测试方案设置起始、目标和步进电流，以及超时和下电参数。'),
('填写合格范围','核对中心波长规格、允许偏差、FWHM 上限、PIB 下限和半带宽。'),
('保存设置','点击“保存设置”。需要独立保存为另一个脚本时，返回后用“添加”另存。')],
note='规格决定“达标 / 超限”的显示。不要为消除红色提示而放宽规格，应先确认产品标准和测量条件。')

page('14  开始一次自动测试','准备完成后，只需一次“开始”。','11-ready.png',[
('核对基本信息','检查 SN、温度、站别、脚本及输出目录；确认本次测量对应正确样品。'),
('核对设备与限值','确认电源在线，功率计有有效读数，所需光谱采集正常；TDK 输出需明确开启。'),
('点击开始','点击设备卡片右侧“开始”。若提示未就绪，按提示补齐条件，再重试。')],crop=(0,0,1440,635),
note='演示环境可能跳过部分真实设备门禁；现场以桌面应用的就绪提示和实际仪器状态为准。')

page('15  观察测试阶段和读数','运行时配置锁定，等待软件依次完成各电流点。','12-running.png',[
('看顶部阶段','观察准备、升流、稳定、停留或记录、完成，以及当前第几个测试点。'),
('看实时变化','核对当前电流、功率曲线、稳定状态及功率 / 效率图；启用光谱时同时查看光谱指标。'),
('处理异常提示','出现超时、掉线或饱和提示时，按提示排查并重测；不能仅凭曲线还在显示就继续。')],crop=(0,0,1440,675),
note='示例回放固定演示 3 个电流点，不代表截图中“标准测试”的实际扫描点数。真实点数由保存的脚本决定。')

page('16  完成后先检查三个结果','完成摘要中的“测试点、数据、安全下电”应分别核对。','14-complete.png',[
('点数完整','核对已保存点数与计划点数。主动停止或异常结束可能只保留部分测试点。'),
('数据已保存','确认本地记录状态和“Excel 已生成”；仍在生成或导出失败时先处理文件问题。'),
('下电已确认','确认安全下电状态；需要时核对电源实体面板。完成流程不等于所有光谱指标都合格。')],crop=(180,92,1248,570),
note='示例中流程完成，但 FWHM 仍显示“超限”。流程状态与产品合格判定必须分开看。')

page('17  打开结果，再测试下一颗','入口：完成摘要 → 详情。','13-complete-dialog.png',[
('查看详情','在“详情”中核对产品 SN、已保存点数、下电状态与结果路径。'),
('打开结果文件','Excel 生成完成后点击“打开结果”。确认文件能打开，SN 和测试点正确。'),
('准备下一颗','确认数据与下电状态后点击“测试下一台”或摘要中的“下一颗”，输入新 SN 再开始。')],tall=True,
note='图中 /preview/ 路径属于演示环境，没有真实 Excel 文件；现场使用结果窗口显示的实际路径。')

page('18  主动停止与急停','正常提前结束用“停止”；需要紧急处理时用右上角“急停”。','14b-stopped.png',[
('正常提前结束','点击“停止”，等待软件结束测试并执行下电。不要把“停止”当作暂停后继续。'),
('确认结束状态','看到“测试已提前结束”后，仍检查点数、文件和下电结果；未测完部分不算完成。'),
('发生紧急情况','点击“急停”并按现场应急规程处理。软件无法确认下电时，检查电源实体面板。')],crop=(445,245,550,540),tall=True,
note='示例立即停止，保存点数为 0。没有有效测试点时，不应期待生成完整测试结果。')

page('19  查找历史测试记录','入口：左侧“数据浏览”。','15-data-browser.png',[
('输入 SN','在“搜索 SN”输入产品编号；按需要选择日期范围和测试类型。'),
('刷新并选择记录','点击“刷新”，按时间、SN 和测试点数选择左侧记录。相同 SN 可能有多次测试。'),
('检查数据来源','列表为空时查看“数据源与更多筛选”，确认数据源、目录和筛选条件。')],crop=(182,96,1242,692),
note='截图使用独立的历史演示记录 DEMO-LD-001，与前面的 DEMO-001 回放不是同一次测试。')

page('20  查看测试点表','入口：数据浏览 → 选中记录 → 测试点。可点击右上角“展开详情”看完整表格。','16-data-points-expanded.png',[
('逐点核对','按电流行查看电压、功率、电光效率及光谱指标。'),
('注意缺失值','某项显示为空或无值时，先核对该次测试是否采集对应数据，不按 0 值理解。'),
('看记录条件','切换“记录信息”检查保存的测试条件；需要追溯同一 SN 时使用“履历”。')],crop=(0,0,1236,696),
note='显示保留的小数位不等于仪器精度。结果解释应结合设备量程、标定和测试条件。')

page('21  查看曲线与某个电流点的光谱','入口：数据浏览 → 曲线分析。图中已展开详情。','17-spectrum-review-expanded.png',[
('选曲线内容','“指标曲线”用于看功率等指标；“单电流光谱”用于查看一个实测点的光谱。'),
('定位实测点','使用下方滑块、前后按钮或“当前测试点”下拉框切换电流点。'),
('核对读数','查看当前点读数和曲线；有光谱保存数据时，还可选择多电流光谱等视图。')],
note='光谱纵轴是原始响应值，不代表绝对光功率。比较前先核对积分时间、光路及其他条件。')

page('22  选择要比较的记录','入口：数据浏览左侧测试记录列表。','18-compare-selection.png',[
('勾选记录','勾选记录左侧复选框。最多选择 5 条记录；先选 2 条更容易核对。'),
('确认选择范围','检查时间、SN 和测试类型；同一 SN 的不同批次或站别要区分。'),
('开始比较','点击左下方比较篮中的“开始比较”；“清空”可重选。')],crop=(189,185,323,798),tall=True,
note='先检查温度、站别、功率系数和光谱采集条件；不同条件下的差异不能直接归因于产品变化。')

page('23  阅读比较结果','选择记录后点击“开始比较”进入叠加曲线。','19-compare.png',[
('确认图例','通过颜色、时间和 SN 识别每条记录，避免把同名记录看成同一次测试。'),
('选比较指标','切换功率、效率、波长等实际存在的指标，并定位相同电流下的测试点。'),
('检查原始记录','遇到明显差异，返回单条记录核对条件、测试点和警告，再决定是否复测。')],crop=(513,183,911,790),
note='本页演示数据只用于说明操作。请按实测点和相同条件比较，不把演示曲线用于性能结论。')

page('24  手动测量并保存测试点','入口：左侧“手动配置”。自动测试运行时，手动硬件控制会锁定。','21-manual.png',[
('准备并采集','填写 SN、测试站别和输出目录；连接电源，启动功率及需要的光谱采集。'),
('设置实际电流','按已确认限值设置电流，等待功率稳定、有效电压与光谱检查完成，出现已暂存提示。'),
('保存当前测试点','按钮可用后点击“保存当前测试点”，确认保存成功。保存本身不代表电源已经关闭。')],crop=(183,96,1244,556),
note='按钮不可用时检查样品信息、目录和是否已有待保存点。测量结束后另行关闭输出并确认安全状态。')

page('25  使用 PD 采集','只在配有 NI-DAQ 采集卡、并已完成接线确认时使用。','22-pd.png',[
('识别采集卡','点击“识别”，选择采集卡、输入通道、接线方式和量程。'),
('填写参数','设置采样率、每批点数、标定比例 / 偏置和单位；需要记录时勾选“保存原始数据”并选目录。'),
('启动与保存','点击“开始采集”观察趋势与统计量；结束时点击“停止并保存”，核对保存提示。')],crop=(180,93,1247,808),
note='PD 采集可独立启停；停止 PD 采集不等于关闭电源输出。接线方式、输入量程按硬件方案确定。')

page('26  生成单个出货报告','入口：左侧“出货报告” → 单个生成。','23-shipping.png',[
('选测试结果','选择软件生成的实际 .xlsx 文件，并选择本地 PDF 保存文件夹。'),
('读取并配置','点击“读取并配置报告”；核对 SN、产品名、报告类型、工作电流及报告参数。'),
('预览并导出','检查实时预览后点击“生成 PDF”；完成后打开结果，核对页码、图表和参数。')],crop=(181,96,1245,404),
note='截图仅展示真实的入口操作，路径为示例；本次未在 Windows 实拍后续报告预览与导出结果。')

page('27  批量生成出货报告','入口：出货报告 → 批量生成。适用于同一批次的结果文件。','24-shipping-batch.png',[
('选择目录','选择 Excel 批量根目录和 PDF 保存文件夹，点击“扫描并预检”。'),
('核对预检结果','检查可生成、忽略和无效项目；按 Excel 内 SN 去重时，软件选择修改时间最新的文件。'),
('确认后批量生成','核对代表文件预览和共用参数后点击“批量生成 PDF”；完成后检查成功、跳过及失败数量。')],crop=(181,96,1245,334),
note='“修改时间最新”不等于客户指定的正式测量批次。应先整理输入目录，再执行批量生成。')

page('28  调整外观与检查授权','入口：左侧“系统设置”。','20-appearance.png',[
('选明暗和配色','根据环境选择深色或浅色主题；设置立即生效、自动保存。'),
('调整显示','按需要调整图表画布、曲线粗细和界面密度；远距离查看可选“远看”。'),
('查看授权','向下滚动到“软件授权”查看有效期；收到新的许可证时使用“重新导入许可证”。')],crop=(180,95,1247,808),
note='显示设置不会替代仪器标定或改变原始测量值。本手册统一采用浅色主题。')

begin('遇到问题，先看这里','按界面提示处理；不要用反复点击“开始”代替排查。','问题速查')
problems=[
('软件打不开','检查 WebView2 是否安装；确认运行的是 Windows 安装后的 Power Test。'),
('许可证无效','检查机器码、授权有效期与文件是否属于当前电脑，联系交付方重新确认。'),
('设备列表为空','检查驱动、USB / 串口线和仪器供电，关闭可能占用仪器的厂商软件后重新识别。'),
('显示在线但没有功率','确认已“开始采集”、探头及资源正确、读数有效；Ophir 还需检查可用校准波长。'),
('光谱饱和 / 测试超时','核对积分时间、光路、仪器状态和测试条件；按提示重测当前点或安全结束。'),
('Excel 导出失败','确认目录可写、磁盘空间充足、文件未被占用；按界面提示重试导出或选择其他目录。'),
('下电未确认','先核对电源实体面板；按界面重试或取消退出。未确认前不能按“已安全关闭”处理。'),
('数据浏览找不到记录','清除 SN / 日期 / 类型筛选，刷新并检查数据源、目录；确认测试确实已保存。')]
y=H-109
for i,(a,b) in enumerate(problems):
    c.setFillColor(PALE if i%2==0 else colors.white);c.rect(28,y-43,W-56,43,fill=1,stroke=0)
    text(a,38,y-9,177,11,bold=True)
    text(b,226,y-9,W-266,10.7,leading=15)
    y-=43
text('联系支持时提供',28,125,150,12,bold=True)
text('完整错误截图、软件版本、仪器型号 / 探头 SN、正在执行的步骤，以及对应测试 SN 和时间。',183,125,W-211,11)
notice('本册已按当前界面与实现核对。实际仪器连通性、测量准确性和 Windows 安装效果仍需现场确认。')
finish('问题速查')

c.save()
(OUT/'guide_manifest.json').write_text(json.dumps({'version':'0.1.2','sourceCommit':'013db5aa','date':'2026-09-28',
    'captureEnvironment':'Current Vite UI on macOS Chromium, actual interactions, built-in virtual demo data',
    'limitations':['No Windows installer screenshots','Activation component in browser preview','No real-device measurements','Shipping export entry only'],
    'pages':audit},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(f'Created {PDF} ({len(audit)} pages)')
