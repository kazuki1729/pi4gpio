//! BCM2711 (Raspberry Pi 4) のハードウェアレジスタに直接触れる層。
//!
//! `unsafe`を要する操作をこのクレートに局所化し、`pi4gpio-daemon`側は
//! 安全なRustのみで書く（SESSION_HANDOFF.md §4-2の言語選定方針）。
#![deny(unsafe_op_in_unsafe_fn)]

pub mod error;
pub mod gpio;
pub mod i2c;
pub mod spi;
pub mod uart;

pub use error::HwError;
