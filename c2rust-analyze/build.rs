use c2rust_build_paths::SysRoot;

fn main() {
    let sysroot = SysRoot::resolve();
    sysroot.link_rustc_private();

    print!("cargo:rustc-env=C2RUST_TARGET_LIB_DIR=");
    print_bytes::println_lossy(
        &sysroot
            .rustlib()
            .as_os_str()
            .to_os_string()
            .as_encoded_bytes()
            .to_vec(),
    );
}
