use base64::{engine::general_purpose::STANDARD as BASE64_STANDARD, Engine as _};
use chrono::{DateTime, Utc};
use ed25519_dalek::{Signature, Verifier, VerifyingKey};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
use tauri::{AppHandle, Manager, State};

const LICENSE_PRODUCT_ID: &str = "arp-power-test";
const LICENSE_FILE_NAME: &str = "license.json";
const LICENSE_EXEMPT_MACHINE_HASHES: &[&str] =
    &["23b84c1e0676071689d5e8d6da3b8911ece54018708f8ba4f66e8b63e6740e60"];
const PUBLIC_KEY_BYTES: [u8; 32] = [
    0x37, 0xd4, 0xe3, 0x81, 0xd2, 0xc4, 0xbd, 0xed, 0x94, 0x91, 0xf5, 0x88, 0xa5, 0x2c, 0xc5, 0x10,
    0x8b, 0x8b, 0x75, 0xef, 0x56, 0xf4, 0xf5, 0x02, 0x19, 0xbb, 0x30, 0x1c, 0xbf, 0x90, 0x3b, 0x72,
];

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct LicensePayload {
    pub product: String,
    pub customer: String,
    pub machine_hash: String,
    pub issued_at: String,
    pub expires_at: String,
    pub features: Vec<String>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct LicenseDocument {
    payload: LicensePayload,
    signature: String,
}

#[derive(Clone, Copy, Debug, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum LicenseStatusReason {
    Valid,
    MachineExempt,
    Missing,
    InvalidFormat,
    InvalidSignature,
    WrongProduct,
    MachineMismatch,
    NotYetValid,
    Expired,
    MachineIdUnavailable,
}

impl LicenseStatusReason {
    pub fn message(self) -> &'static str {
        match self {
            Self::Valid => "许可证有效",
            Self::MachineExempt => "本机免授权",
            Self::Missing => "未安装许可证",
            Self::InvalidFormat => "许可证格式不正确",
            Self::InvalidSignature => "许可证签名无效",
            Self::WrongProduct => "许可证不属于当前软件",
            Self::MachineMismatch => "许可证不属于当前设备",
            Self::NotYetValid => "许可证尚未生效",
            Self::Expired => "许可证已过期",
            Self::MachineIdUnavailable => "无法读取当前设备机器码",
        }
    }
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct LicenseStatus {
    pub valid: bool,
    pub reason: LicenseStatusReason,
    pub machine_hash: String,
    pub machine_code: String,
    pub license_path: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub payload: Option<LicensePayload>,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct CanonicalLicensePayload<'a> {
    product: &'a str,
    customer: &'a str,
    machine_hash: &'a str,
    issued_at: &'a str,
    expires_at: &'a str,
    features: Vec<&'a str>,
}

#[derive(Clone, Debug)]
struct MachineIdentity {
    hash: String,
    code: String,
}

pub struct LicenseState {
    machine: Result<MachineIdentity, String>,
}

impl LicenseState {
    pub fn new() -> Self {
        Self {
            machine: machine_identity(),
        }
    }

    pub fn status(&self, app: &AppHandle) -> LicenseStatus {
        let license_path = license_path(app);
        let Ok(machine) = &self.machine else {
            return LicenseStatus {
                valid: false,
                reason: LicenseStatusReason::MachineIdUnavailable,
                machine_hash: String::new(),
                machine_code: "机器码不可用".to_string(),
                license_path: license_path.display().to_string(),
                payload: None,
            };
        };

        if is_machine_exempt(&machine.hash) {
            return status_for(
                machine,
                &license_path,
                LicenseStatusReason::MachineExempt,
                None,
            );
        }

        if !license_path.exists() {
            return status_for(machine, &license_path, LicenseStatusReason::Missing, None);
        }

        let document = match read_license_document(&license_path) {
            Ok(document) => document,
            Err(_) => {
                return status_for(
                    machine,
                    &license_path,
                    LicenseStatusReason::InvalidFormat,
                    None,
                )
            }
        };
        let result = verify_license_document(
            &document,
            &PUBLIC_KEY_BYTES,
            LICENSE_PRODUCT_ID,
            &machine.hash,
            Utc::now(),
        );
        status_for(machine, &license_path, result.reason, result.payload)
    }
}

struct VerificationResult {
    reason: LicenseStatusReason,
    payload: Option<LicensePayload>,
}

impl VerificationResult {
    fn valid(payload: LicensePayload) -> Self {
        Self {
            reason: LicenseStatusReason::Valid,
            payload: Some(payload),
        }
    }

    fn invalid(reason: LicenseStatusReason) -> Self {
        Self {
            reason,
            payload: None,
        }
    }
}

fn status_for(
    machine: &MachineIdentity,
    license_path: &Path,
    reason: LicenseStatusReason,
    payload: Option<LicensePayload>,
) -> LicenseStatus {
    LicenseStatus {
        valid: matches!(
            reason,
            LicenseStatusReason::Valid | LicenseStatusReason::MachineExempt
        ),
        reason,
        machine_hash: machine.hash.clone(),
        machine_code: machine.code.clone(),
        license_path: license_path.display().to_string(),
        payload,
    }
}

fn is_machine_exempt(machine_hash: &str) -> bool {
    LICENSE_EXEMPT_MACHINE_HASHES.contains(&machine_hash)
}

fn canonicalize_payload(payload: &LicensePayload) -> Result<Vec<u8>, serde_json::Error> {
    let mut features = payload
        .features
        .iter()
        .map(String::as_str)
        .collect::<Vec<_>>();
    features.sort_unstable();
    serde_json::to_vec(&CanonicalLicensePayload {
        product: &payload.product,
        customer: &payload.customer,
        machine_hash: &payload.machine_hash,
        issued_at: &payload.issued_at,
        expires_at: &payload.expires_at,
        features,
    })
}

fn verify_license_document(
    document: &LicenseDocument,
    public_key_bytes: &[u8; 32],
    expected_product: &str,
    current_machine_hash: &str,
    now: DateTime<Utc>,
) -> VerificationResult {
    let payload = &document.payload;
    if payload.product.trim().is_empty()
        || payload.customer.trim().is_empty()
        || payload.machine_hash.trim().is_empty()
        || payload.issued_at.trim().is_empty()
        || payload.expires_at.trim().is_empty()
        || payload.features.is_empty()
        || payload
            .features
            .iter()
            .any(|feature| feature.trim().is_empty())
    {
        return VerificationResult::invalid(LicenseStatusReason::InvalidFormat);
    }

    let issued_at = match DateTime::parse_from_rfc3339(&payload.issued_at) {
        Ok(value) => value.with_timezone(&Utc),
        Err(_) => return VerificationResult::invalid(LicenseStatusReason::InvalidFormat),
    };
    let expires_at = match DateTime::parse_from_rfc3339(&payload.expires_at) {
        Ok(value) => value.with_timezone(&Utc),
        Err(_) => return VerificationResult::invalid(LicenseStatusReason::InvalidFormat),
    };
    if expires_at < issued_at {
        return VerificationResult::invalid(LicenseStatusReason::InvalidFormat);
    }

    let verifying_key = match VerifyingKey::from_bytes(public_key_bytes) {
        Ok(key) => key,
        Err(_) => return VerificationResult::invalid(LicenseStatusReason::InvalidSignature),
    };
    let signature_bytes = match BASE64_STANDARD.decode(&document.signature) {
        Ok(value) => value,
        Err(_) => return VerificationResult::invalid(LicenseStatusReason::InvalidSignature),
    };
    let signature = match Signature::from_slice(&signature_bytes) {
        Ok(value) => value,
        Err(_) => return VerificationResult::invalid(LicenseStatusReason::InvalidSignature),
    };
    let message = match canonicalize_payload(payload) {
        Ok(value) => value,
        Err(_) => return VerificationResult::invalid(LicenseStatusReason::InvalidFormat),
    };
    if verifying_key.verify(&message, &signature).is_err() {
        return VerificationResult::invalid(LicenseStatusReason::InvalidSignature);
    }
    if payload.product != expected_product {
        return VerificationResult::invalid(LicenseStatusReason::WrongProduct);
    }
    if payload.machine_hash != current_machine_hash {
        return VerificationResult::invalid(LicenseStatusReason::MachineMismatch);
    }
    if issued_at > now {
        return VerificationResult::invalid(LicenseStatusReason::NotYetValid);
    }
    if expires_at < now {
        return VerificationResult::invalid(LicenseStatusReason::Expired);
    }
    VerificationResult::valid(payload.clone())
}

fn parse_license_document(bytes: &[u8]) -> Result<LicenseDocument, String> {
    let text = if bytes.starts_with(&[0xff, 0xfe]) {
        let units = bytes[2..]
            .chunks_exact(2)
            .map(|pair| u16::from_le_bytes([pair[0], pair[1]]))
            .collect::<Vec<_>>();
        String::from_utf16(&units).map_err(|error| error.to_string())?
    } else if bytes.len() >= 4 && bytes[1] == 0 && bytes[3] == 0 {
        let units = bytes
            .chunks_exact(2)
            .map(|pair| u16::from_le_bytes([pair[0], pair[1]]))
            .collect::<Vec<_>>();
        String::from_utf16(&units).map_err(|error| error.to_string())?
    } else {
        String::from_utf8(bytes.to_vec()).map_err(|error| error.to_string())?
    };
    serde_json::from_str(text.trim_start_matches('\u{feff}')).map_err(|error| error.to_string())
}

fn read_license_document(path: &Path) -> Result<LicenseDocument, String> {
    let bytes = fs::read(path).map_err(|error| error.to_string())?;
    parse_license_document(&bytes)
}

fn license_path(app: &AppHandle) -> PathBuf {
    app.path()
        .app_data_dir()
        .unwrap_or_else(|_| PathBuf::from("."))
        .join(LICENSE_FILE_NAME)
}

fn read_command(program: &str, args: &[&str]) -> Result<String, String> {
    let output = Command::new(program)
        .args(args)
        .output()
        .map_err(|error| error.to_string())?;
    if !output.status.success() {
        return Err(format!("{program} 返回状态 {}", output.status));
    }
    let value = String::from_utf8(output.stdout)
        .map_err(|error| error.to_string())?
        .trim()
        .to_string();
    if value.is_empty() {
        Err(format!("{program} 未返回设备标识"))
    } else {
        Ok(value)
    }
}

#[cfg(target_os = "windows")]
fn platform_machine_id() -> Result<String, String> {
    read_command(
        "powershell.exe",
        &[
            "-NoProfile",
            "-Command",
            "(Get-CimInstance Win32_ComputerSystemProduct).UUID",
        ],
    )
}

#[cfg(target_os = "macos")]
fn platform_machine_id() -> Result<String, String> {
    let output = read_command("ioreg", &["-rd1", "-c", "IOPlatformExpertDevice"])?;
    output
        .lines()
        .find(|line| line.contains("IOPlatformUUID"))
        .and_then(|line| line.split('"').nth(3))
        .map(str::to_string)
        .ok_or_else(|| "ioreg 未返回 IOPlatformUUID".to_string())
}

#[cfg(all(unix, not(target_os = "macos")))]
fn platform_machine_id() -> Result<String, String> {
    for path in ["/etc/machine-id", "/var/lib/dbus/machine-id"] {
        if let Ok(value) = fs::read_to_string(path) {
            let value = value.trim();
            if !value.is_empty() {
                return Ok(value.to_string());
            }
        }
    }
    Err("系统未提供 machine-id".to_string())
}

fn machine_identity() -> Result<MachineIdentity, String> {
    let machine_id = platform_machine_id()?;
    let hash = format!(
        "{:x}",
        Sha256::digest(format!("{LICENSE_PRODUCT_ID}:{machine_id}").as_bytes())
    );
    let grouped = hash
        .to_uppercase()
        .as_bytes()
        .chunks(4)
        .map(|chunk| String::from_utf8_lossy(chunk).into_owned())
        .collect::<Vec<_>>()
        .join("-");
    Ok(MachineIdentity {
        hash,
        code: format!("ARP-{grouped}"),
    })
}

#[tauri::command]
pub fn license_get_status(app: AppHandle, state: State<'_, LicenseState>) -> LicenseStatus {
    state.status(&app)
}

#[tauri::command]
pub fn license_install(
    source_path: String,
    app: AppHandle,
    state: State<'_, LicenseState>,
) -> Result<LicenseStatus, String> {
    let source = PathBuf::from(source_path);
    let document = read_license_document(&source).map_err(|_| "许可证格式不正确".to_string())?;
    let machine = state
        .machine
        .as_ref()
        .map_err(|error| format!("无法生成机器码：{error}"))?;
    let destination = license_path(&app);
    let result = verify_license_document(
        &document,
        &PUBLIC_KEY_BYTES,
        LICENSE_PRODUCT_ID,
        &machine.hash,
        Utc::now(),
    );
    if result.reason != LicenseStatusReason::Valid {
        return Ok(status_for(machine, &destination, result.reason, None));
    }

    let parent = destination
        .parent()
        .ok_or_else(|| "许可证目录不可用".to_string())?;
    fs::create_dir_all(parent).map_err(|error| format!("无法创建许可证目录：{error}"))?;
    let temporary = destination.with_extension("json.tmp");
    fs::write(
        &temporary,
        fs::read(&source).map_err(|error| error.to_string())?,
    )
    .map_err(|error| format!("无法暂存许可证：{error}"))?;
    if destination.exists() {
        fs::remove_file(&destination).map_err(|error| format!("无法替换旧许可证：{error}"))?;
    }
    fs::rename(&temporary, &destination).map_err(|error| format!("无法安装许可证：{error}"))?;
    Ok(state.status(&app))
}

#[cfg(test)]
mod tests {
    use super::*;
    use ed25519_dalek::{Signer, SigningKey};

    fn signed_document(payload: LicensePayload) -> (LicenseDocument, [u8; 32]) {
        let signing_key = SigningKey::from_bytes(&[7_u8; 32]);
        let signature = signing_key.sign(&canonicalize_payload(&payload).unwrap());
        (
            LicenseDocument {
                payload,
                signature: BASE64_STANDARD.encode(signature.to_bytes()),
            },
            signing_key.verifying_key().to_bytes(),
        )
    }

    fn payload() -> LicensePayload {
        LicensePayload {
            product: LICENSE_PRODUCT_ID.to_string(),
            customer: "测试客户".to_string(),
            machine_hash: "machine-a".to_string(),
            issued_at: "2026-08-01T00:00:00Z".to_string(),
            expires_at: "2027-08-01T00:00:00Z".to_string(),
            features: vec!["standard".to_string()],
        }
    }

    #[test]
    fn accepts_a_valid_signed_license() {
        let (document, public_key) = signed_document(payload());
        let result = verify_license_document(
            &document,
            &public_key,
            LICENSE_PRODUCT_ID,
            "machine-a",
            "2026-08-24T00:00:00Z".parse().unwrap(),
        );
        assert_eq!(result.reason, LicenseStatusReason::Valid);
        assert_eq!(result.payload.unwrap().customer, "测试客户");
    }

    #[test]
    fn rejects_tampering_wrong_machine_and_expiry() {
        let (mut document, public_key) = signed_document(payload());
        document.payload.customer = "被篡改".to_string();
        assert_eq!(
            verify_license_document(
                &document,
                &public_key,
                LICENSE_PRODUCT_ID,
                "machine-a",
                "2026-08-24T00:00:00Z".parse().unwrap(),
            )
            .reason,
            LicenseStatusReason::InvalidSignature
        );

        let (document, public_key) = signed_document(payload());
        assert_eq!(
            verify_license_document(
                &document,
                &public_key,
                LICENSE_PRODUCT_ID,
                "machine-b",
                "2026-08-24T00:00:00Z".parse().unwrap(),
            )
            .reason,
            LicenseStatusReason::MachineMismatch
        );
        assert_eq!(
            verify_license_document(
                &document,
                &public_key,
                LICENSE_PRODUCT_ID,
                "machine-a",
                "2028-08-24T00:00:00Z".parse().unwrap(),
            )
            .reason,
            LicenseStatusReason::Expired
        );
    }

    #[test]
    fn parses_utf8_bom_and_utf16le_documents() {
        let (document, _) = signed_document(payload());
        let json = serde_json::to_string(&document).unwrap();
        let utf8 = format!("\u{feff}{json}");
        assert_eq!(
            parse_license_document(utf8.as_bytes()).unwrap().payload,
            document.payload
        );

        let mut utf16 = vec![0xff, 0xfe];
        for unit in json.encode_utf16() {
            utf16.extend_from_slice(&unit.to_le_bytes());
        }
        assert_eq!(
            parse_license_document(&utf16).unwrap().payload,
            document.payload
        );
    }

    #[test]
    fn bundled_public_key_accepts_the_offline_javascript_issuer() {
        let document = LicenseDocument {
            payload: LicensePayload {
                product: LICENSE_PRODUCT_ID.to_string(),
                customer: "跨语言验签".to_string(),
                machine_hash: "ab".repeat(32),
                issued_at: "2026-08-24T00:00:00.000Z".to_string(),
                expires_at: "2027-08-24T00:00:00.000Z".to_string(),
                features: vec!["standard".to_string()],
            },
            signature: "/Hnc/vUaDCQXKOyzwlBPT5vxKdR6DKiainJHoPUNRjN+fov7ZdK5AJC0+zUVq2dAQgBHyNbpaNgs2wMdchpsCA==".to_string(),
        };
        assert_eq!(
            verify_license_document(
                &document,
                &PUBLIC_KEY_BYTES,
                LICENSE_PRODUCT_ID,
                &"ab".repeat(32),
                "2026-08-24T12:00:00Z".parse().unwrap(),
            )
            .reason,
            LicenseStatusReason::Valid
        );
    }

    #[test]
    fn exemption_is_limited_to_the_explicit_current_machine_hash() {
        assert!(is_machine_exempt(
            "23b84c1e0676071689d5e8d6da3b8911ece54018708f8ba4f66e8b63e6740e60"
        ));
        assert!(!is_machine_exempt(&"ab".repeat(32)));
    }
}
