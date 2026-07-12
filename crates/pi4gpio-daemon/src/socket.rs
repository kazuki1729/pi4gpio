//! Unixドメインソケットサーバ。
//!
//! NETWORK_POLICY.mdの決定に基づき、この段階ではローカルソケットのみを実装対象と
//! する。Tailscale限定bindは実際にリモート制御が必要になった時点で追加する。

use crate::client::ClientId;
use crate::config::Config;
use crate::lock::{BusId, LockTable};
use crate::protocol::{BusRef, EdgeEventWire, Operation, Request, Response};
use pi4gpio_hw::gpio::{GpioChip, Level, PullMode};
use pi4gpio_hw::gpio_watch::EdgeWatcher;
use pi4gpio_hw::i2c::I2cBus;
use pi4gpio_hw::spi::SpiDevice;
use pi4gpio_hw::uart::UartPort;
use std::collections::hash_map::Entry;
use std::collections::{HashMap, HashSet};
use std::io;
use std::sync::{Arc, Mutex};
use std::time::Duration;
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::net::{UnixListener, UnixStream};
use tokio::signal::unix::{signal, SignalKind};

const GPIOCHIP_PATH: &str = "/dev/gpiochip0";

/// I2Cバスはリクエストで指定された`bus`番号ごとに初回アクセス時に開く
/// （`/dev/i2c-0`と`/dev/i2c-1`の両方が存在しうるため、GPIOのようにプロセス
/// 起動時点で単一インスタンスを確保する構成にできない）。
type I2cBuses = HashMap<u8, I2cBus>;
/// SPIも同様に`(bus, chip_select)`ごとに初回アクセス時に開く。
type SpiDevices = HashMap<(u8, u8), SpiDevice>;
/// UARTも同様に`port`番号ごとに初回アクセス時に開く。`port`は
/// `/dev/ttyS{port}`に対応する（daemon側の命名規約）。
type UartPorts = HashMap<u8, UartPort>;

/// 各バス種別のハードウェア状態をまとめて保持する。ハンドラ関数の引数が
/// バス種別の数だけ増え続けるのを避けるための1つの塊として扱う。
struct Peripherals {
    locks: LockTable,
    gpio: Mutex<GpioChip>,
    i2c: Mutex<I2cBuses>,
    spi: Mutex<SpiDevices>,
    uart: Mutex<UartPorts>,
}

pub async fn serve(config: &Config) -> io::Result<()> {
    if let Some(parent) = std::path::Path::new(&config.socket_path).parent() {
        std::fs::create_dir_all(parent)?;
    }
    let _ = std::fs::remove_file(&config.socket_path);

    let listener = UnixListener::bind(&config.socket_path)?;
    println!("pi4gpiod: listening on {}", config.socket_path);

    let peripherals = Arc::new(Peripherals {
        locks: LockTable::new(),
        gpio: Mutex::new(GpioChip::open().map_err(|e| io::Error::other(e.to_string()))?),
        i2c: Mutex::new(HashMap::new()),
        spi: Mutex::new(HashMap::new()),
        uart: Mutex::new(HashMap::new()),
    });
    let mut sigterm = signal(SignalKind::terminate())?;

    loop {
        tokio::select! {
            accepted = listener.accept() => {
                let (stream, _addr) = accepted?;
                let peripherals = Arc::clone(&peripherals);
                tokio::spawn(async move {
                    if let Err(err) = handle_client(stream, peripherals).await {
                        eprintln!("pi4gpiod: client session ended with error: {err}");
                    }
                });
            }
            _ = sigterm.recv() => {
                println!("pi4gpiod: received SIGTERM, shutting down");
                break;
            }
            _ = tokio::signal::ctrl_c() => {
                println!("pi4gpiod: received SIGINT, shutting down");
                break;
            }
        }
    }

    let _ = std::fs::remove_file(&config.socket_path);
    Ok(())
}

async fn handle_client(stream: UnixStream, peripherals: Arc<Peripherals>) -> io::Result<()> {
    let client_id = ClientId::from_unix_stream(&stream)?;
    println!("pi4gpiod: client connected ({client_id:?})");

    let (reader, mut writer) = stream.into_split();
    let mut lines = BufReader::new(reader).lines();
    let mut held_buses: HashSet<BusId> = HashSet::new();

    while let Some(line) = lines.next_line().await? {
        if line.trim().is_empty() {
            continue;
        }

        let response = match serde_json::from_str::<Request>(&line) {
            Ok(request) => {
                // Tier 2のWatchEdgesは最大で数十ミリ秒ブロックしうる
                // （Tier 1の各操作はマイクロ秒オーダーで無視できる差だが、
                // これは無視できない）。tokioのワーカースレッドを塞がない
                // よう、実際のディスパッチはブロッキングスレッドプールで行う。
                let peripherals = Arc::clone(&peripherals);
                let client_id = client_id.clone();
                let mut held = std::mem::take(&mut held_buses);
                let (response, held) = tokio::task::spawn_blocking(move || {
                    let response = dispatch(&request, &client_id, &peripherals, &mut held);
                    (response, held)
                })
                .await
                .expect("dispatch task panicked");
                held_buses = held;
                response
            }
            Err(err) => Response::malformed(&err.to_string()),
        };

        let mut payload =
            serde_json::to_vec(&response).expect("Response serialization cannot fail");
        payload.push(b'\n');
        writer.write_all(&payload).await?;
    }

    for bus in held_buses.drain() {
        peripherals.locks.release(bus, &client_id);
    }
    println!("pi4gpiod: client disconnected ({client_id:?})");
    Ok(())
}

fn dispatch(
    request: &Request,
    client_id: &ClientId,
    peripherals: &Peripherals,
    held_buses: &mut HashSet<BusId>,
) -> Response {
    let bus: BusId = (&request.bus).into();

    if matches!(request.op, Operation::Release) {
        peripherals.locks.release(bus, client_id);
        held_buses.remove(&bus);
        return Response::ok();
    }

    if !held_buses.contains(&bus) {
        match peripherals.locks.try_acquire(bus, client_id.clone()) {
            Ok(()) => {
                held_buses.insert(bus);
            }
            Err(holder) => return Response::locked_by(&format!("{holder:?}")),
        }
    }

    match &request.bus {
        BusRef::Gpio { pin } => handle_gpio(*pin, &request.op, &peripherals.gpio),
        BusRef::I2c { bus, addr } => handle_i2c(*bus, *addr, &request.op, &peripherals.i2c),
        BusRef::Spi { bus, chip_select } => {
            handle_spi(*bus, *chip_select, &request.op, &peripherals.spi)
        }
        BusRef::Uart { port, baud_rate } => {
            handle_uart(*port, *baud_rate, &request.op, &peripherals.uart)
        }
    }
}

fn handle_gpio(pin: u32, op: &Operation, gpio: &Mutex<GpioChip>) -> Response {
    match op {
        Operation::Read | Operation::Write { .. } => {
            let mut chip = gpio.lock().expect("gpio mutex poisoned");
            let result = match op {
                Operation::Read => chip
                    .claim_input(pin, PullMode::None)
                    .and_then(|()| chip.read(pin))
                    .map(|level| level == Level::High),
                Operation::Write { value } => {
                    let level = if *value { Level::High } else { Level::Low };
                    chip.claim_output(pin)
                        .and_then(|()| chip.write(pin, level))
                        .map(|()| *value)
                }
                _ => unreachable!(),
            };
            match result {
                Ok(value) => Response::value(value),
                Err(err) => Response::hw_error(&err.to_string()),
            }
        }
        Operation::WatchEdges {
            pre_pulse_low_ms,
            max_events,
            timeout_ms,
        } => handle_watch_edges(pin, *pre_pulse_low_ms, *max_events, *timeout_ms, gpio),
        Operation::ReadBytes { .. }
        | Operation::WriteBytes { .. }
        | Operation::WriteReadBytes { .. }
        | Operation::Transfer { .. } => Response::malformed("gpioバスにはバイト列操作は使えません"),
        Operation::Release => unreachable!("Releaseはdispatchの時点で処理済み"),
    }
}

/// スタート信号（任意）を送ってからエッジを記録する（Tier 2、DHT22向け）。
///
/// `pre_pulse_low_ms`が指定されていれば、`/dev/gpiomem`経由（Tier 1、
/// `gpio.rs`）でピンをLOW出力にしてから待ち、その後`/dev/gpiochip0`経由
/// （Tier 2、`gpio_watch.rs`）に切り替えてエッジ監視を開始する。両者は
/// カーネルのピン使用状況把握という点で別経路のため、この切り替え自体は
/// カーネル側の衝突検知（EBUSY）の対象にならない（`gpio_watch.rs`のモジュール
/// docを参照）。このピンの`LockTable`ロックは呼び出し元がスタート信号から
/// 監視終了まで保持しているため、他クライアントの割り込みは防がれている。
fn handle_watch_edges(
    pin: u32,
    pre_pulse_low_ms: Option<u64>,
    max_events: usize,
    timeout_ms: u64,
    gpio: &Mutex<GpioChip>,
) -> Response {
    if let Some(ms) = pre_pulse_low_ms {
        let mut chip = gpio.lock().expect("gpio mutex poisoned");
        let result = chip
            .claim_output(pin)
            .and_then(|()| chip.write(pin, Level::Low));
        drop(chip); // sleep中は他バスのGPIO操作をブロックしない。
        if let Err(err) = result {
            return Response::hw_error(&err.to_string());
        }
        std::thread::sleep(Duration::from_millis(ms));
    }

    let watcher = EdgeWatcher::open(GPIOCHIP_PATH, pin);
    match watcher.and_then(|mut w| w.wait_events(Duration::from_millis(timeout_ms), max_events)) {
        Ok(events) => Response::edges(
            events
                .into_iter()
                .map(|e| EdgeEventWire {
                    timestamp_ns: e.timestamp_ns,
                    rising: e.rising,
                })
                .collect(),
        ),
        Err(err) => Response::hw_error(&err.to_string()),
    }
}

fn handle_i2c(bus_num: u8, addr: u8, op: &Operation, i2c: &Mutex<I2cBuses>) -> Response {
    let mut buses = i2c.lock().expect("i2c mutex poisoned");
    let bus = match buses.entry(bus_num) {
        Entry::Occupied(entry) => entry.into_mut(),
        Entry::Vacant(entry) => match I2cBus::open(bus_num) {
            Ok(opened) => entry.insert(opened),
            Err(err) => return Response::hw_error(&err.to_string()),
        },
    };

    match op {
        Operation::ReadBytes { length } => {
            let mut buf = vec![0u8; *length];
            match bus.read(addr, &mut buf) {
                Ok(()) => Response::bytes(buf),
                Err(err) => Response::hw_error(&err.to_string()),
            }
        }
        Operation::WriteBytes { data } => match bus.write(addr, data) {
            Ok(()) => Response::ok(),
            Err(err) => Response::hw_error(&err.to_string()),
        },
        Operation::WriteReadBytes { data, length } => {
            let mut buf = vec![0u8; *length];
            match bus.write_read(addr, data, &mut buf) {
                Ok(()) => Response::bytes(buf),
                Err(err) => Response::hw_error(&err.to_string()),
            }
        }
        Operation::Read
        | Operation::Write { .. }
        | Operation::Transfer { .. }
        | Operation::WatchEdges { .. } => Response::malformed("i2cバスにはこの操作は使えません"),
        Operation::Release => unreachable!("Releaseはdispatchの時点で処理済み"),
    }
}

fn handle_spi(bus_num: u8, chip_select: u8, op: &Operation, spi: &Mutex<SpiDevices>) -> Response {
    let mut devices = spi.lock().expect("spi mutex poisoned");
    let device = match devices.entry((bus_num, chip_select)) {
        Entry::Occupied(entry) => entry.into_mut(),
        Entry::Vacant(entry) => match SpiDevice::open(bus_num, chip_select) {
            Ok(opened) => entry.insert(opened),
            Err(err) => return Response::hw_error(&err.to_string()),
        },
    };

    match op {
        Operation::Transfer { data } => {
            let mut rx = vec![0u8; data.len()];
            match device.transfer(data, &mut rx) {
                Ok(()) => Response::bytes(rx),
                Err(err) => Response::hw_error(&err.to_string()),
            }
        }
        Operation::Read
        | Operation::Write { .. }
        | Operation::ReadBytes { .. }
        | Operation::WriteBytes { .. }
        | Operation::WriteReadBytes { .. }
        | Operation::WatchEdges { .. } => Response::malformed("spiバスにはこの操作は使えません"),
        Operation::Release => unreachable!("Releaseはdispatchの時点で処理済み"),
    }
}

fn handle_uart(port: u8, baud_rate: u32, op: &Operation, uart: &Mutex<UartPorts>) -> Response {
    let mut ports = uart.lock().expect("uart mutex poisoned");
    let device_path = format!("/dev/ttyS{port}");
    let opened = match ports.entry(port) {
        Entry::Occupied(entry) => entry.into_mut(),
        Entry::Vacant(entry) => match UartPort::open(&device_path, baud_rate) {
            Ok(opened) => entry.insert(opened),
            Err(err) => return Response::hw_error(&err.to_string()),
        },
    };

    match op {
        Operation::ReadBytes { length } => {
            let mut buf = vec![0u8; *length];
            match opened.read(&mut buf) {
                Ok(n) => Response::bytes(buf[..n].to_vec()),
                Err(err) => Response::hw_error(&err.to_string()),
            }
        }
        Operation::WriteBytes { data } => match opened.write(data) {
            Ok(_) => Response::ok(),
            Err(err) => Response::hw_error(&err.to_string()),
        },
        Operation::Read
        | Operation::Write { .. }
        | Operation::WriteReadBytes { .. }
        | Operation::Transfer { .. }
        | Operation::WatchEdges { .. } => Response::malformed("uartバスにはこの操作は使えません"),
        Operation::Release => unreachable!("Releaseはdispatchの時点で処理済み"),
    }
}
