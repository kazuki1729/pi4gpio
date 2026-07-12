//! Unixドメインソケットサーバ。
//!
//! NETWORK_POLICY.mdの決定に基づき、この段階ではローカルソケットのみを実装対象と
//! する。Tailscale限定bindは実際にリモート制御が必要になった時点で追加する。

use crate::client::ClientId;
use crate::config::Config;
use crate::lock::{BusId, LockTable};
use crate::protocol::{BusRef, Operation, Request, Response};
use pi4gpio_hw::gpio::{GpioChip, Level, PullMode};
use std::collections::HashSet;
use std::io;
use std::sync::{Arc, Mutex};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::net::{UnixListener, UnixStream};
use tokio::signal::unix::{signal, SignalKind};

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
    let mut sigterm = signal(SignalKind::terminate())?;

    loop {
        tokio::select! {
            accepted = listener.accept() => {
                let (stream, _addr) = accepted?;
                let locks = Arc::clone(&locks);
                let gpio = Arc::clone(&gpio);
                tokio::spawn(async move {
                    if let Err(err) = handle_client(stream, locks, gpio).await {
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
            Ok(request) => dispatch(&request, &client_id, &locks, &mut held_buses, &gpio),
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
        // I2C/SPI/UARTはpi4gpio-hw側が未実装のため引き続きnot_implemented。
        BusRef::I2c { .. } | BusRef::Spi { .. } | BusRef::Uart { .. } => {
            Response::not_implemented()
        }
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
        Operation::Release => unreachable!("Releaseはdispatchの時点で処理済み"),
    };

    match result {
        Ok(value) => Response::value(value),
        Err(err) => Response::hw_error(&err.to_string()),
    }
}
