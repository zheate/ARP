//! Configuration labels and grouping. Python remains the validator.
pub const GENERAL: &[(&str, &str)] = &[
    ("sn", "SN"),
    ("productModel", "产品型号"),
    ("batch", "批次"),
    ("station", "站别"),
    ("testTemperatureC", "测试温度"),
    ("outputDir", "Excel 输出目录"),
    ("selectedScriptId", "测试脚本"),
    ("initialCurrentA", "起始电流 / A"),
    ("targetCurrentA", "目标电流 / A"),
    ("currentStepA", "电流步进 / A"),
    ("currentPlanMode", "电流计划"),
    ("customCurrentPoints", "自定义电流点与等待秒数"),
    ("pointTimeoutS", "单点超时 / s"),
];
pub const SUPPLY: &[(&str, &str)] = &[
    ("powerSupplyKind", "电源控制器"),
    ("ch341SelectedLocationPath", "CH341 物理端口"),
    ("ch341PortBindings", "CH341 端口名称"),
    ("tdkResource", "TDK 串口资源"),
    ("tdkVoltageV", "TDK 输出电压 / V"),
    ("ruResource", "RU 串口资源"),
    ("ruBaudRate", "RU 波特率"),
    ("ruAddress", "RU 地址"),
    ("ruVoltageV", "RU 输出电压 / V"),
    ("setCurrentA", "设定电流 / A"),
];
pub const METER: &[(&str, &str)] = &[
    ("powerMeterResource", "功率计资源"),
    ("powerMeterWavelengthNm", "测量波长 / nm"),
    ("powerMeterIntervalMs", "功率采样间隔 / ms"),
    ("powerSmoothing", "功率曲线平滑"),
    ("stableWindowS", "判稳窗口 / s"),
    ("stabilityLimitPercent", "稳定度上限 / %"),
    ("powerStabilityAbsoluteFloorW", "功率绝对阈值 / W"),
];
pub const SPECTRUM: &[(&str, &str)] = &[
    ("spectrometerResource", "光谱仪资源"),
    ("integrationTimeUs", "积分时间 / μs"),
    ("autoIntegration", "自动积分"),
    ("spectrometerIntervalMs", "光谱采样间隔 / ms"),
    ("spectrumAverageFrames", "平均帧数"),
    ("spectrumSmoothing", "光谱曲线平滑"),
    ("pibCenterWavelengthNm", "PIB 中心波长 / nm"),
    ("pibHalfRangeNm", "PIB 半宽 / nm"),
];
pub const SAFETY: &[(&str, &str)] = &[
    ("rampDownStepA", "下电电流步进 / A"),
    ("rampDownIntervalS", "下电步进间隔 / s"),
    ("pauseRampDownTimeoutS", "暂停自动下电超时 / s"),
    ("centerWavelengthSpecNm", "中心波长规格 / nm"),
    ("centerWavelengthToleranceNm", "中心波长容差 / nm"),
    ("fwhmMaximumNm", "FWHM 上限 / nm"),
    ("pibMinimumPercent", "PIB 下限 / %"),
];
pub const PD: &[(&str, &str)] = &[
    ("device", "DAQ 设备"),
    ("channel", "采集通道"),
    ("terminal", "接线模式"),
    ("range", "输入量程 / V"),
    ("sampleRateHz", "采样率 / Hz"),
    ("blockSize", "每块样本数"),
    ("scale", "比例系数"),
    ("offset", "偏移量"),
    ("unit", "单位"),
    ("save", "保存 CSV"),
    ("outputDir", "CSV 输出目录"),
];
pub const PROFILE: &[(&str, &str)] = &[
    ("name", "方案名称"),
    ("softwareGain", "软件增益"),
    ("wavelengthNm", "标定波长 / nm"),
    ("notes", "条件说明"),
    ("operator", "操作人"),
    ("reason", "修订原因"),
];
