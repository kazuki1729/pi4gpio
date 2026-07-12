//! Unixドメインソケットサーバ。
//!
//! NETWORK_POLICY.mdの決定に基づき、この段階ではローカルソケットのみを実装対象と
//! する。Tailscale限定bindは実際にリモート制御が必要になった時点で追加する。

use crate::client::ClientId;
use crate::config::Config;
use crate::lock::{BusId, LockTable};
use crate::protocol::{BusRef, Operation, Request, Response};
use pi4gpio_hw::gpio::{GpioChip, Level, PullMode};
use pi4gpio_hw::i2c::I2cBus;
use pi4gpio_hw::spi::SpiDevice;
use std::collections::hash_map::Entry;
use std::collections::{HashMap, HashSet};
use std::io;
use std::sync::{Arc, Mutex};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::net::{UnixListener, UnixStream};
use tokio::signal::unix::{signal, SignalKind};

/// I2Cバスはリクエストで指定された`bus`番号ごとに初回アクセス時に開く
/// （`/dev/i2c-0`と`/dev/i2c-1`の両方が存在しうるため、GPIOのようにプロセス
/// 起動時点で単一インスタンスを確保する構成にできない）。
type I2cBuses = HashMap<u8, I2cBus>;
/// SPIも同様に`(bus, chip_select)`ごとに初回アクセス時に開く。
type SpiDevices = HashMap<(u8, u8), SpiDevice>;

pub async fn serve(config: &Config) -> io::Result<()> {
    if let Some(parent) = std::path::Path::new(&config.socket_path).parent() {
        std::fs::create_dir_all(parent)?;
    }
    let _ = std::fs::remove_file(&config.socket_path);

    let listener = UnixListener::bind(&config.socket_path)?;
    println!("pi4gpiod: listening on {}", config.socket_path);

    let locks = Arc::new(LockTable::new());
    let gpio = Arc::new(Mutex::new(
        GpioChip::open().map_err(|e| io::Error::other(e.to_string()))?,
    ));
    let i2c: Arc<Mutex<I2cBuses>> = Arc::new(Mutex::new(HashMap::new()));
    let spi: Arc<Mutex<SpiDevices>> = Arc::new(Mutex::new(HashMap::new()));
    let mut sigterm = signal(SignalKind::terminate())?;

    loop {
        tokio::select! {
            accepted = listener.accept() => {
                let (stream, _addr) = accepted?;
                let locks = Arc::clone(&locks);
                let gpio = Arc::clone(&gpio);
                let i2c = Arc::clone(&i2c);
                let spi = Arc::clone(&spi);
                tokio::spawn(async move {
                    if let Err(err) = handle_client(stream, locks, gpio, i2c, spi).await {
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

async fn handle_client(
    stream: UnixStream,
    locks: Arc<LockTable>,
    gpio: Arc<Mutex<GpioChip>>,
    i2c: Arc<Mutex<I2cBuses>>,
    spi: Arc<Mutex<SpiDevices>>,
) -> io::Result<()> {
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
            Ok(request) => dispatch(
                &request,
                &client_id,
                &locks,
                &mut held_buses,
                &gpio,
                &i2c,
                &spi,
            ),
            Err(err) => Response::malformed(&err.to_string()),
        };

        let mut payload =
            serde_json::to_vec(&response).expect("Response serialization cannot fail");
        payload.push(b'\n');
        writer.write_all(&payload).await?;
    }

    for bus in held_buses.drain() {
        locks.release(bus, &client_id);
    }
    println!("pi4gpiod: client disconnected ({client_id:?})");
    Ok(())
}

fn dispatch(
    request: &Request,
    client_id: &ClientId,
    locks: &LockTable,
    held_buses: &mut HashSet<BusId>,
    gpio: &Mutex<GpioChip>,
    i2c: &Mutex<I2cBuses>,
    spi: &Mutex<SpiDevices>,
) -> Response {
    let bus: BusId = (&request.bus).into();

    if matches!(request.op, Operation::Release) {
        locks.release(bus, client_id);
        held_buses.remove(&bus);
        return Response::ok();
    }

    if !held_buses.contains(&bus) {
        match locks.try_acquire(bus, client_id.clone()) {
            Ok(()) => {
                held_buses.insert(bus);
            }
            Err(holder) => return Response::locked_by(&format!("{holder:?}")),
        }
    }

    match &request.bus {
        BusRef::Gpio { pin } => handle_gpio(*pin, &request.op, gpio),
        BusRef::I2c { bus, addr } => handle_i2c(*bus, *addr, &request.op, i2c),
        BusRef::Spi { bus, chip_select } => handle_spi(*bus, *chip_select, &request.op, spi),
        // UARTはpi4gpio-hw側が未実装のため引き続きnot_implemented。
        BusRef::Uart { .. } => Response::not_implemented(),
    }
}

fn handle_gpio(pin: u32, op: &Operation, gpio: &Mutex<GpioChip>) -> Response {
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
        Operation::ReadBytes { .. }
        | Operation::WriteBytes { .. }
        | Operation::WriteReadBytes { .. }
        | Operation::Transfer { .. } => {
            return Response::malformed("gpioバスにはバイト列操作は使えません");
        }
        Operation::Release => unreachable!("Releaseはdispatchの時点で処理済み"),
    };

    match result {
        Ok(value) => Response::value(value),
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
        Operation::Read | Operation::Write { .. } | Operation::Transfer { .. } => {
            Response::malformed("i2cバスにはこの操作は使えません")
        }
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
        | Operation::WriteReadBytes { .. } => {
            Response::malformed("spiバスにはこの操作は使えません")
        }
        Operation::Release => unreachable!("Releaseはdispatchの時点で処理済み"),
    }
}
