use std::env;
use std::sync::{
    atomic::{AtomicBool, Ordering},
    Arc,
};
use std::thread;
use std::time::{Duration, Instant};

use windows::Win32::Foundation::{BOOL, HWND, LPARAM};
use windows::Win32::Graphics::Gdi::{GetDC, GetPixel, ReleaseDC};
use windows::Win32::System::Threading::{
    GetCurrentProcess, GetCurrentThread, SetPriorityClass, SetThreadAffinityMask,
    HIGH_PRIORITY_CLASS,
};
use windows::Win32::UI::Input::KeyboardAndMouse::{
    GetAsyncKeyState, MapVirtualKeyW, SendInput, INPUT, INPUT_0, INPUT_KEYBOARD, KEYBDINPUT,
    KEYEVENTF_KEYUP, KEYEVENTF_SCANCODE, KEYBD_EVENT_FLAGS, MAPVK_VK_TO_VSC, VIRTUAL_KEY,
    VK_ESCAPE,
};
use windows::Win32::UI::WindowsAndMessaging::{
    EnumWindows, GetForegroundWindow, GetWindowTextLengthW, GetWindowTextW, IsWindowVisible,
};

#[derive(Clone, Debug)]
struct Lane {
    x: i32,
    vk: VIRTUAL_KEY,
    scan: u16,
}

#[derive(Clone, Debug)]
struct Config {
    lanes: Vec<Lane>,
    hit_zone_y: i32,

    brightness_thresh: i32,
    tolerance: i32,
    brightness_thresh3: i32,
    white_min: i32,

    min_hold: Duration,
    release_debounce: Duration,

    focus_poll: Duration,
    not_focused_sleep: Duration,

    loop_yield_every: u32,
    roblox_title_substr: String,
}

fn env_u32(name: &str, default: u32) -> u32 {
    match env::var(name) {
        Ok(v) => v.trim().parse::<u32>().unwrap_or(default),
        Err(_) => default,
    }
}

fn env_i32(name: &str, default: i32) -> i32 {
    match env::var(name) {
        Ok(v) => v.trim().parse::<i32>().unwrap_or(default),
        Err(_) => default,
    }
}

fn env_string(name: &str, default: &str) -> String {
    env::var(name).unwrap_or_else(|_| default.to_string())
}

fn parse_vk(key: &str) -> Result<VIRTUAL_KEY, String> {
    let k = key.trim();
    if k.len() != 1 {
        return Err(format!("Unsupported key (expected single char): {k}"));
    }
    let c = k.chars().next().unwrap().to_ascii_lowercase();
    if !c.is_ascii_alphanumeric() {
        return Err(format!("Unsupported key (a-z/0-9 only): {k}"));
    }
    Ok(VIRTUAL_KEY(c.to_ascii_uppercase() as u16))
}

fn parse_lanes(spec: &str) -> Result<Vec<Lane>, String> {
    // Format: "731:e,881:r,1031:t,1181:y"
    let mut lanes = Vec::new();
    for part in spec.split(',').map(|s| s.trim()).filter(|s| !s.is_empty()) {
        let (x_str, key_str) = part
            .split_once(':')
            .ok_or_else(|| format!("Bad lane entry (expected x:key): {part}"))?;
        let x = x_str
            .trim()
            .parse::<i32>()
            .map_err(|_| format!("Bad x coordinate: {x_str}"))?;
        let vk = parse_vk(key_str)?;
        let scan = unsafe { MapVirtualKeyW(vk.0 as u32, MAPVK_VK_TO_VSC) as u16 };
        lanes.push(Lane { x, vk, scan });
    }
    if lanes.is_empty() {
        return Err("No lanes parsed from LANES".to_string());
    }
    Ok(lanes)
}

fn load_config() -> Result<Config, String> {
    let lanes_spec = env_string("LANES", "731:e,881:r,1031:t,1181:y");
    let lanes = parse_lanes(&lanes_spec)?;

    let hit_zone_y = env_i32("HIT_ZONE_Y", 880);

    let brightness_thresh = env_i32("BRIGHTNESS_THRESH", 240);
    let tolerance = env_i32("TOLERANCE", 120);
    let brightness_thresh3 = brightness_thresh * 3;
    let white_min = 255 - tolerance;

    let min_hold = Duration::from_millis(env_u32("MIN_HOLD_MS", 20) as u64);
    let release_debounce = Duration::from_millis(env_u32("RELEASE_DEBOUNCE_MS", 12) as u64);
    let focus_poll = Duration::from_millis(env_u32("FOCUS_POLL_MS", 50) as u64);
    let not_focused_sleep = Duration::from_millis(env_u32("NOT_FOCUSED_SLEEP_MS", 100) as u64);

    let loop_yield_every = env_u32("LOOP_YIELD_EVERY", 0);

    let roblox_title_substr = env_string("ROBLOX_TITLE_SUBSTR", "Roblox");

    Ok(Config {
        lanes,
        hit_zone_y,
        brightness_thresh,
        tolerance,
        brightness_thresh3,
        white_min,
        min_hold,
        release_debounce,
        focus_poll,
        not_focused_sleep,
        loop_yield_every,
        roblox_title_substr,
    })
}

unsafe fn set_high_priority() {
    let _ = SetPriorityClass(GetCurrentProcess(), HIGH_PRIORITY_CLASS);
}

unsafe fn send_vk(vk: VIRTUAL_KEY, down: bool) {
    let mut ki = KEYBDINPUT::default();
    ki.wVk = vk;
    ki.dwFlags = if down { KEYBD_EVENT_FLAGS(0) } else { KEYEVENTF_KEYUP };

    let mut input = INPUT::default();
    input.r#type = INPUT_KEYBOARD;
    input.Anonymous = INPUT_0 { ki };

    // Ignore errors; worst case is missed input which is acceptable here.
    let _ = SendInput(&[input], std::mem::size_of::<INPUT>() as i32);
}

unsafe fn send_lane_key(lane: &Lane, down: bool) {
    // Prefer scan code injection (often more reliable in games). Fall back to VK if mapping fails.
    let mut ki = KEYBDINPUT::default();
    if lane.scan != 0 {
        ki.wVk = VIRTUAL_KEY(0);
        ki.wScan = lane.scan;
        ki.dwFlags = if down {
            KEYEVENTF_SCANCODE
        } else {
            KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP
        };
    } else {
        ki.wVk = lane.vk;
        ki.dwFlags = if down { KEYBD_EVENT_FLAGS(0) } else { KEYEVENTF_KEYUP };
    }

    let mut input = INPUT::default();
    input.r#type = INPUT_KEYBOARD;
    input.Anonymous = INPUT_0 { ki };
    let _ = SendInput(&[input], std::mem::size_of::<INPUT>() as i32);
}

fn is_white(cfg: &Config, r: i32, g: i32, b: i32) -> bool {
    if r + g + b > cfg.brightness_thresh3 {
        return true;
    }
    r >= cfg.white_min && g >= cfg.white_min && b >= cfg.white_min
}

unsafe fn hwnd_title_contains(hwnd: HWND, needle: &str) -> bool {
    let len = GetWindowTextLengthW(hwnd);
    if len <= 0 {
        return false;
    }
    let mut buf: Vec<u16> = vec![0; (len as usize) + 1];
    let got = GetWindowTextW(hwnd, &mut buf);
    if got <= 0 {
        return false;
    }
    let s = String::from_utf16_lossy(&buf[..got as usize]);
    s.contains(needle)
}

unsafe extern "system" fn enum_windows_cb(hwnd: HWND, lparam: LPARAM) -> BOOL {
    if !IsWindowVisible(hwnd).as_bool() {
        return BOOL(1);
    }
    let target = &*(lparam.0 as *const EnumSearch);
    if hwnd_title_contains(hwnd, &target.title_substr) {
        target.found.store(hwnd.0 as isize, Ordering::SeqCst);
        return BOOL(0); // stop
    }
    BOOL(1)
}

struct EnumSearch {
    title_substr: String,
    found: std::sync::atomic::AtomicIsize,
}

unsafe fn find_roblox_hwnd(title_substr: &str) -> Option<HWND> {
    let search = EnumSearch {
        title_substr: title_substr.to_string(),
        found: std::sync::atomic::AtomicIsize::new(0),
    };
    let lp = LPARAM((&search as *const EnumSearch) as isize);
    let _ = EnumWindows(Some(enum_windows_cb), lp);
    let v = search.found.load(Ordering::SeqCst);
    if v != 0 {
        Some(HWND(v))
    } else {
        None
    }
}

unsafe fn is_roblox_focused(roblox_hwnd: Option<HWND>, title_substr: &str) -> bool {
    let fg = GetForegroundWindow();
    if let Some(h) = roblox_hwnd {
        if fg == h {
            return true;
        }
    }
    hwnd_title_contains(fg, title_substr)
}

fn main() -> Result<(), String> {
    let cfg = load_config()?;

    unsafe { set_high_priority() };

    let roblox_hwnd = unsafe { find_roblox_hwnd(&cfg.roblox_title_substr) };
    match roblox_hwnd {
        Some(h) => println!("[INFO] Roblox HWND found: {}", h.0),
        None => println!(
            "[INFO] Roblox HWND not found; falling back to title substring: {}",
            cfg.roblox_title_substr
        ),
    }

    let debug = env_u32("DEBUG", 0) != 0;
    let running = Arc::new(AtomicBool::new(true));
    let focused = Arc::new(AtomicBool::new(false));

    // Focus watcher: one Win32 focus check for all lanes.
    {
        let running = Arc::clone(&running);
        let focused = Arc::clone(&focused);
        let cfg2 = cfg.clone();
        thread::spawn(move || {
            let mut last = false;
            while running.load(Ordering::Relaxed) {
                let ok = unsafe { is_roblox_focused(roblox_hwnd, &cfg2.roblox_title_substr) };
                focused.store(ok, Ordering::Relaxed);
                if debug && ok != last {
                    println!("[FOCUS] {}", if ok { "on" } else { "off" });
                    last = ok;
                }
                thread::sleep(cfg2.focus_poll);
            }
        });
    }

    // ESC watcher: stop the program.
    {
        let running = Arc::clone(&running);
        thread::spawn(move || {
            while running.load(Ordering::Relaxed) {
                // High bit is set when key is down (i16 becomes negative).
                let state = unsafe { GetAsyncKeyState(VK_ESCAPE.0 as i32) };
                if state < 0 {
                    running.store(false, Ordering::Relaxed);
                    break;
                }
                thread::sleep(Duration::from_millis(5));
            }
        });
    }

    let core_count = thread::available_parallelism().map(|n| n.get()).unwrap_or(1) as u64;
    println!(
        "[INFO] Starting {} lanes at y={}, logical_cores={}",
        cfg.lanes.len(),
        cfg.hit_zone_y,
        core_count
    );
    println!("[INFO] Press ESC to stop.");

    let mut handles = Vec::new();
    for (idx, lane) in cfg.lanes.iter().cloned().enumerate() {
        let running = Arc::clone(&running);
        let focused = Arc::clone(&focused);
        let cfg2 = cfg.clone();

        let handle = thread::spawn(move || unsafe {
            // Try to pin each lane to its own core (best-effort).
            if core_count > 0 {
                let core = (idx as u64) % core_count;
                let mask: usize = 1usize << core;
                let _ = SetThreadAffinityMask(GetCurrentThread(), mask);
            }

            let hdc = GetDC(HWND(0));
            let mut key_pressed = false;
            let mut last_white = Instant::now();
            let mut press_time = Instant::now();
            let mut iters: u32 = 0;

            while running.load(Ordering::Relaxed) {
                if !focused.load(Ordering::Relaxed) {
                    if key_pressed {
                        send_lane_key(&lane, false);
                        key_pressed = false;
                        if debug {
                            println!("[LANE {idx}] up (focus)");
                        }
                    }
                    thread::sleep(cfg2.not_focused_sleep);
                    continue;
                }

                let now = Instant::now();
                let color = GetPixel(hdc, lane.x, cfg2.hit_zone_y).0;
                // COLORREF is 0x00bbggrr
                let r = (color & 0xFF) as i32;
                let g = ((color >> 8) & 0xFF) as i32;
                let b = ((color >> 16) & 0xFF) as i32;
                let white = is_white(&cfg2, r, g, b);

                if white {
                    last_white = now;
                    if !key_pressed {
                        send_lane_key(&lane, true);
                        key_pressed = true;
                        press_time = now;
                        if debug {
                            println!("[LANE {idx}] down");
                        }
                    }
                } else if key_pressed {
                    if now.duration_since(last_white) >= cfg2.release_debounce
                        && now.duration_since(press_time) >= cfg2.min_hold
                    {
                        send_lane_key(&lane, false);
                        key_pressed = false;
                        if debug {
                            println!("[LANE {idx}] up");
                        }
                    }
                }

                iters = iters.wrapping_add(1);
                if cfg2.loop_yield_every != 0 && (iters % cfg2.loop_yield_every) == 0 {
                    thread::yield_now();
                }
            }

            if key_pressed {
                send_lane_key(&lane, false);
            }
            let _ = ReleaseDC(HWND(0), hdc);
        });
        handles.push(handle);
    }

    for h in handles {
        let _ = h.join();
    }
    Ok(())
}
