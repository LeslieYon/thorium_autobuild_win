#!/usr/bin/env python3
"""
Merge single-file patches into multi-file patches organized by purpose.

This script reads the auto-generated single-file patches from patches/thorium/
and merges them into multi-file patches based on their purpose/theme.

Reference categories from patches/thorium/original/ are used to determine
whether to merge into existing patches or create new ones.
"""

import os
import shutil
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
PATCHES_DIR = BASE / "patches"
THORIUM_DIR = PATCHES_DIR / "thorium"
ORIGINAL_DIR = THORIUM_DIR / "original"
MERGED_DIR = THORIUM_DIR / "merged"
SERIES_FILE = PATCHES_DIR / "series"
SERIES_BACKUP = PATCHES_DIR / "series.bak"

# Mapping of categories to their constituent patches
# Each entry: output_filename -> list of (relative_path_to_patch, description)
CATEGORIES = {
    # === MERGE INTO EXISTING thorium_branding.patch ===
    "thorium_branding_append": {
        "output": ORIGINAL_DIR / "thorium_branding.patch",
        "mode": "append",
        "description": "Branding: product name, executables, desktop integration, installer, API keys",
        "patches": [
            # Build target branding
            ("config/BUILD.gn.patch", "Thorium build target and sandbox group"),
            # Android branding
            ("config/chrome__android__BUILD.gn.patch", "Remove branded drawable resources"),
            ("config/chrome__android__chrome_public_apk_tmpl.gni.patch", "Package name org.chromium.thorium"),
            ("config/components__BUILD.gn.patch", "Content Shell -> Thorium Shell"),
            # Windows branding
            ("fixes/chrome__app__chrome_exe.vsprops.patch", "thorium.exe output name"),
            ("fixes/chrome__chrome_proxy__chrome_proxy_main_win.cc.patch", "thorium.exe proxy reference"),
            ("fixes/chrome__install_static__chromium_install_modes.cc.patch", "Thorium install mode"),
            ("fixes/chrome__install_static__chromium_install_modes.h.patch", "Thorium install mode header"),
            ("fixes/chrome__install_static__user_data_dir_win_unittest.cc.patch", "Thorium user data dir test"),
            ("fixes/chrome__installer__launcher_support__chrome_launcher_support.cc.patch", "Launcher support registry keys"),
            # Cross-platform branding
            ("fixes/chrome__app__chrome_main_delegate.cc.patch", "Thorium logging brand name"),
            ("fixes/chrome__app__resources__manpage.1.in.patch", "Thorium manpage URL"),
            ("fixes/chrome__browser__memory_details_linux.cc.patch", "thorium process name"),
            ("fixes/chrome__browser__shell_integration_linux.cc.patch", "Thorium Browser desktop"),
            ("fixes/chrome__common__channel_info_posix.cc.patch", "thorium-browser.desktop"),
            ("fixes/chrome__common__chrome_constants.cc.patch", "kBrandName constant"),
            ("fixes/chrome__common__chrome_constants.h.patch", "kBrandName header"),
            ("fixes/chrome__common__chrome_paths_linux.cc.patch", "thorium config directory"),
            ("fixes/chrome__common__chrome_paths_mac.mm.patch", "Thorium product directory"),
            # Linux packaging branding
            ("fixes/chrome__installer__linux__common__apt.include.patch", "APT include branding"),
            ("fixes/chrome__installer__linux__common__chromium-browser.info.patch", "chromium-browser.info branding"),
            ("fixes/chrome__installer__linux__common__installer.include.patch", "Installer include branding"),
            ("fixes/chrome__installer__linux__common__repo.cron.patch", "Repo cron branding"),
            ("fixes/chrome__installer__linux__common__rpm.include.patch", "RPM include branding"),
            ("fixes/chrome__installer__linux__common__rpmrepo.cron.patch", "RPM repo cron branding"),
            ("fixes/chrome__installer__linux__common__wrapper.patch", "Wrapper script branding"),
            ("fixes/chrome__installer__linux__debian__additional_deps.patch", "Debian deps branding"),
            ("fixes/chrome__installer__linux__debian__build.sh.patch", "Debian build branding"),
            ("fixes/chrome__installer__linux__debian__postinst.patch", "Debian postinst branding"),
            ("fixes/chrome__installer__linux__debian__postrm.patch", "Debian postrm branding"),
            ("fixes/chrome__installer__linux__debian__prerm.patch", "Debian prerm branding"),
            ("fixes/chrome__installer__linux__rpm__build.sh.patch", "RPM build branding"),
            # API keys
            ("fixes/google_apis__default_api_keys-inc.cc.patch", "Thorium API keys"),
            ("fixes/google_apis__default_api_keys.h.patch", "Thorium API keys header"),
            # Content shell branding
            ("features/content__shell__android__BUILD.gn.patch", "Thorium Shell APK name"),
            ("features/content__shell__android__shell_apk__AndroidManifest.xml.jinja2.patch", "Thorium Shell app label"),
            ("features/content__shell__app__shell.rc.patch", "Thorium Shell icon"),
            ("features/content__shell__app__shell_main_delegate.cc.patch", "Thorium Shell log name"),
            ("features/content__shell__browser__shell_platform_delegate_views.cc.patch", "Thorium Shell WM class"),
            ("features/content__shell__BUILD.gn.patch", "Thorium Shell build product name"),
            ("features/content__test__BUILD.gn.patch", "Thorium Shell test deps"),
            # Windows build tool branding
            ("windows/build__win__reorder-imports.py.patch", "thorium.exe reorder-imports"),
            # Browser test branding
            ("features/content__browser__launch_as_mojo_client_browsertest.cc.patch", "thorium_shell test name"),
        ],
    },

    # === NEW multi-file patches ===
    "thorium-compiler-simd": {
        "output": MERGED_DIR / "thorium-compiler-simd.patch",
        "mode": "create",
        "description": "Compiler/SIMD optimization flags",
        "patches": [
            ("compiler/build__config__android__BUILD.gn.patch", "Android compiler flags and official build"),
            ("compiler/build__config__arm.gni.patch", "ARM FPU/NEON defaults removal"),
            ("compiler/build__config__BUILDCONFIG.gn.patch", "SIMD optimization config"),
            ("compiler/build__config__compiler__BUILD.gn.patch", "Compiler build config"),
            ("compiler/build__config__mac__BUILD.gn.patch", "macOS compiler cflags/ldflags"),
            ("compiler/build__config__win__BUILD.gn.patch", "Windows SSE/MMX/AVX flags"),
            ("v8/v8__BUILD.gn.patch", "V8 SSE2/SSE3/AVX flags"),
            ("fixes/tools__clang__scripts__build.py.patch", "LLVM/clang AVX/AES/Polly flags"),
        ],
    },

    "thorium-build-config": {
        "output": MERGED_DIR / "thorium-build-config.patch",
        "mode": "create",
        "description": "Build system configuration (non-branding)",
        "patches": [
            ("config/ash__webui__sample_system_web_app_ui__BUILD.gn.patch", "Ash webui BUILD.gn"),
            ("config/ash__webui__sample_system_web_app_ui__mojom__BUILD.gn.patch", "Ash webui mojom BUILD.gn"),
            ("config/ash__webui__sample_system_web_app_ui__resources__trusted__BUILD.gn.patch", "Ash webui resources trusted"),
            ("config/ash__webui__sample_system_web_app_ui__resources__untrusted__BUILD.gn.patch", "Ash webui resources untrusted"),
            ("config/build__install-build-deps.py.patch", "Linux build deps (advancecomp, icoutils)"),
            ("config/build__toolchain__apple__linker_driver.py.patch", "Apple linker Rustc arg filter"),
            ("config/build__vs_toolchain.py.patch", "VS toolchain hash update"),
            ("config/chrome__browser__BUILD.gn.patch", "Thorium flag entries in browser build"),
            ("config/chrome__installer__linux__BUILD.gn.patch", "Linux installer compiler_opt config"),
            ("config/components__vector_icons__BUILD.gn.patch", "Vector icons build config"),
            ("config/sandbox__linux__BUILD.gn.patch", "Linux sandbox PIE flags"),
            ("config/third_party__widevine__cdm__BUILD.gn.patch", "Widevine CDM build config"),
            ("config/third_party__widevine__cdm__widevine.gni.patch", "Widevine platform enablement"),
            ("config/tools__v8_context_snapshot__BUILD.gn.patch", "V8 snapshot rpath config"),
            ("fixes/third_party__widevine__README.chromium.patch", "Widevine version update"),
        ],
    },

    "thorium-ui": {
        "output": MERGED_DIR / "thorium-ui.patch",
        "mode": "create",
        "description": "UI customizations (toolbar, tabs, omnibox, startup, themes)",
        "patches": [
            ("ui/chrome__browser__ui__browser.cc.patch", "Multi-tab closure confirmation"),
            ("ui/chrome__browser__ui__browser.h.patch", "CanCloseWithMultipleTabs declaration"),
            ("ui/chrome__browser__ui__browser_commands.cc.patch", "Remove content restrictions for Save As"),
            ("ui/chrome__browser__ui__browser_ui_prefs.cc.patch", "Home button default enabled"),
            ("ui/chrome__browser__ui__startup__bad_flags_prompt.cc.patch", "Disable extension/blink flag warnings"),
            ("ui/chrome__browser__ui__startup__default_browser_prompt__default_browser_prompt.cc.patch", "Disable default browser prompt"),
            ("ui/chrome__browser__ui__startup__google_api_keys_infobar_delegate.cc.patch", "Disable API keys infobar"),
            ("ui/chrome__browser__ui__startup__infobar_utils.cc.patch", "Comments out obsolete OS warning"),
            ("ui/chrome__browser__ui__tabs__features.cc.patch", "Scrollable tab strip"),
            ("ui/chrome__browser__ui__tabs__tab_strip_model.cc.patch", "Close window with last tab flag"),
            ("ui/chrome__browser__ui__tabs__tab_strip_prefs.cc.patch", "Left-align tab search"),
            ("ui/chrome__browser__ui__toolbar__app_menu_model.cc.patch", "Regular icon variant"),
            ("ui/chrome__browser__ui__toolbar__chrome_labs__chrome_labs_utils.cc.patch", "Force enable Chrome Labs"),
            ("ui/chrome__browser__ui__toolbar__chrome_location_bar_model_delegate.cc.patch", "No URL elisions in omnibox"),
            ("ui/chrome__browser__ui__ui_features.cc.patch", "UI features TODOs"),
            ("ui/chrome__browser__ui__views__frame__browser_root_view.cc.patch", "Scroll tabs flag"),
            ("ui/chrome__browser__ui__views__frame__browser_root_view.h.patch", "Scroll tabs member variable"),
            ("ui/chrome__browser__ui__views__tabs__tab_strip.cc.patch", "Force disable tab outlines"),
            ("ui/chrome__browser__ui__views__toolbar__browser_app_menu_button.cc.patch", "Thorium icon flag"),
            ("ui/chrome__browser__ui__views__toolbar__home_button.cc.patch", "Thorium home icon flag"),
            ("ui/chrome__browser__ui__views__toolbar__reload_button.cc.patch", "Thorium reload icon"),
            ("ui/chrome__browser__ui__views__toolbar__reload_button.h.patch", "Reload icon flag variable"),
            ("ui/chrome__browser__ui__webui__whats_new__whats_new_util.cc.patch", "Force enable What's New"),
            ("ui/ui__base__x__x11_util.cc.patch", "X11 custom titlebar disable"),
            ("ui/ui__gtk__native_theme_gtk.cc.patch", "Auto dark mode flag for GTK"),
            ("ui/ui__views__examples__BUILD.gn.patch", "Thorium UI debug shell rename"),
            ("ui/ui__views__examples__examples_window.cc.patch", "Debug shell window title"),
            ("ui/ui__webui__resources__images__BUILD.gn.patch", "Hazard SVG icon"),
            # Related UI fixes
            ("fixes/chrome__browser__themes__theme_helper_win.cc.patch", "Windows titlebar config"),
            ("fixes/chrome__browser__permissions__quiet_notification_permission_ui_state.cc.patch", "Quiet notification UI defaults"),
            ("fixes/components__omnibox__browser__omnibox_view.cc.patch", "Remove Google search icon handling"),
        ],
    },

    "thorium-privacy": {
        "output": MERGED_DIR / "thorium-privacy.patch",
        "mode": "create",
        "description": "Privacy features (DoNotTrack, IP protection, ad-auction disable)",
        "patches": [
            ("privacy/components__privacy_sandbox__tracking_protection_prefs.cc.patch", "Enable DNT and IP Protection by default"),
            ("fixes/third_party__blink__common__features.cc.patch", "Disable Ad-Auction-Signals"),
            ("fixes/chrome__browser__net__profile_network_context_service.cc.patch", "Disable alternate error pages"),
            ("fixes/components__offline_pages__core__offline_page_model.cc.patch", "Remove HTTP/HTTPS scheme restriction"),
        ],
    },

    "thorium-media-codecs": {
        "output": MERGED_DIR / "thorium-media-codecs.patch",
        "mode": "create",
        "description": "Media codec support (HEVC, AC3/EAC3, accelerated encoding)",
        "patches": [
            ("media/media__base__media_switches.cc.patch", "Enable tab muting and accelerated video encode"),
            ("media/media__base__supported_types.cc.patch", "HEVC decoder support"),
            ("media/media__ffmpeg__ffmpeg_common.cc.patch", "HEVC decoder + AC3/EAC3 demuxer"),
            ("media/media__filters__ffmpeg_glue.cc.patch", "AC3/EAC3 audio demuxer"),
            ("media/media__filters__ffmpeg_video_decoder.cc.patch", "HEVC codec in ffmpeg decoder"),
            ("media/media__media_options.gni.patch", "CDM assertion update"),
        ],
    },

    "thorium-quarantine-removal": {
        "output": MERGED_DIR / "thorium-quarantine-removal.patch",
        "mode": "create",
        "description": "Remove quarantine service dependency",
        "patches": [
            ("features/content__browser__BUILD.gn.patch", "Remove quarantine build dep"),
            ("features/content__browser__file_system_access__file_system_access_safe_move_helper.cc.patch", "Remove quarantine from file move"),
            ("features/content__browser__file_system_access__file_system_access_safe_move_helper.h.patch", "Remove quarantine mojo remote"),
            ("features/content__browser__renderer_host__pepper__pepper_file_io_host.cc.patch", "Disable PPAPI quarantining"),
            ("features/content__browser__renderer_host__pepper__pepper_file_io_host.h.patch", "Remove quarantine include from PPAPI"),
            ("fixes/components__download__internal__common__base_file.cc.patch", "Remove quarantine from download base_file"),
        ],
    },

    "thorium-extensions-mv3": {
        "output": MERGED_DIR / "thorium-extensions-mv3.patch",
        "mode": "create",
        "description": "Extension MV3 support (Manifest V2 keep-alive, rule limits, warnings)",
        "patches": [
            ("features/extensions__browser__extension_prefs.cc.patch", "Enable Manifest V2 availability"),
            ("features/extensions__browser__ui_util.cc.patch", "Allow hosted apps on non-unpacked locations"),
            ("features/extensions__common__api__declarative_net_request.idl.patch", "Double MV3 rule limits"),
            ("features/extensions__common__extension.cc.patch", "Silence deprecated MV2 warnings"),
            ("fixes/chrome__browser__extensions__component_extensions_allowlist__allowlist.cc.patch", "Whitelist Thorium Hangouts ID"),
            ("fixes/chrome__browser__extensions__extension_management_internal.h.patch", "Default to Manifest V2 enabled"),
        ],
    },

    "thorium-dns-network": {
        "output": MERGED_DIR / "thorium-dns-network.patch",
        "mode": "create",
        "description": "DNS and network configuration (DoH, secure mode, minimal headers)",
        "patches": [
            ("features/net__base__load_flags_list.h.patch", "Add LOAD_MINIMAL_HEADERS flag"),
            ("features/net__dns__dns_client.cc.patch", "Thorium DNS config override"),
            ("features/net__dns__dns_transaction.cc.patch", "LOAD_MINIMAL_HEADERS in DoH"),
            ("features/net__url_request__url_request_http_job.cc.patch", "Remove UA/Referer with MINIMAL_HEADERS"),
            ("features/net__cert__x509_util.cc.patch", "Increase RSA key length to 2048"),
            ("fixes/chrome__browser__net__default_dns_over_https_config_source.cc.patch", "Secure DoH by default"),
            ("fixes/chrome__browser__net__stub_resolver_config_reader.cc.patch", "Remove Windows parental DoH check"),
            ("features/content__common__url_schemes.cc.patch", "Expand saveable URL schemes"),
            ("features/content__public__common__url_utils.cc.patch", "All URLs saveable"),
        ],
    },

    "thorium-downloads": {
        "output": MERGED_DIR / "thorium-downloads.patch",
        "mode": "create",
        "description": "Download behavior fixes (insecure downloads, download features)",
        "patches": [
            ("fixes/chrome__browser__download__chrome_download_manager_delegate.cc.patch", "Allow insecure downloads flag"),
            ("fixes/chrome__browser__download__download_target_determiner.cc.patch", "Download target determiner fixes"),
            ("fixes/chrome__browser__download__download_ui_controller.cc.patch", "Download UI controller"),
            ("fixes/chrome__browser__download__insecure_download_blocking.cc.patch", "Insecure download blocking"),
            ("fixes/components__download__public__common__download_features.cc.patch", "Download features config"),
        ],
    },

    "thorium-encryption-machineid": {
        "output": MERGED_DIR / "thorium-encryption-machineid.patch",
        "mode": "create",
        "description": "Encryption and machine ID disabling",
        "patches": [
            ("fixes/components__metrics__machine_id_provider_nonwin.cc.patch", "Disable machine ID for non-Windows"),
            ("fixes/components__metrics__machine_id_provider_win.cc.patch", "Disable machine ID for Windows"),
            ("fixes/components__os_crypt__async__browser__dpapi_key_provider.cc.patch", "Disable DPAPI encryption flag"),
            ("fixes/components__os_crypt__sync__os_crypt_win.cc.patch", "Disable Windows encryption flag"),
            ("fixes/services__preferences__tracked__device_id_win.cc.patch", "Disable machine ID pref tracking"),
            ("fixes/services__preferences__tracked__tracked_split_preference.cc.patch", "Disable preference validation for flags"),
        ],
    },

    "thorium-history-preferences": {
        "output": MERGED_DIR / "thorium-history-preferences.patch",
        "mode": "create",
        "description": "History and preferences (bookmark bar, history retention, background mode)",
        "patches": [
            ("fixes/components__bookmarks__browser__bookmark_utils.cc.patch", "Show bookmark bar by default, no tab groups"),
            ("fixes/components__history__core__browser__history_backend.cc.patch", "Keep all history flag"),
            ("fixes/components__history__core__browser__history_backend.h.patch", "Increase history threshold to 120 days"),
            ("fixes/chrome__browser__background__extensions__background_mode_manager.cc.patch", "Disable background mode by default"),
            ("fixes/chrome__browser__obsolete_system__obsolete_system_linux.cc.patch", "Comment out Ubuntu 18.04 as obsolete"),
        ],
    },

    "thorium-telemetry-flags": {
        "output": MERGED_DIR / "thorium-telemetry-flags.patch",
        "mode": "create",
        "description": "Telemetry disabling and feature flag changes",
        "patches": [
            ("fixes/chrome__browser__about_flags.cc.patch", "Thorium flag entries in about:flags"),
            ("fixes/chrome__browser__ash__settings__stats_reporting_controller.cc.patch", "Disable stats reporting in ThoriumOS"),
            ("fixes/components__dom_distiller__core__dom_distiller_features.cc.patch", "Reader mode flag for desktop"),
            ("fixes/components__optimization_guide__core__optimization_guide_features.cc.patch", "macOS optimization fix"),
            ("fixes/components__user_education__common__feature_promo__impl__feature_promo_controller_20.cc.patch", "Block all feature promos"),
            ("fixes/components__variations__service__variations_service.cc.patch", "Disable variations seed fetching"),
            ("fixes/components__webui__flags__flags_state.cc.patch", "custom-ntp flag handling"),
            ("fixes/tools__pgo__generate_profile.py.patch", "PGO script python3 shebang"),
        ],
    },

    "thorium-gpu-features": {
        "output": MERGED_DIR / "thorium-gpu-features.patch",
        "mode": "create",
        "description": "GPU/Vulkan/VAAPI content features",
        "patches": [
            ("features/content__gpu__BUILD.gn.patch", "GPU build with VAAPI config"),
            ("features/content__common__gpu_pre_sandbox_hook_linux.cc.patch", "Vulkan ICD paths in GPU sandbox"),
        ],
    },

    "thorium-misc-fixes": {
        "output": MERGED_DIR / "thorium-misc-fixes.patch",
        "mode": "create",
        "description": "Miscellaneous fixes (custom NTP, sandbox, webaudio, Windows sandbox)",
        "patches": [
            ("fixes/chrome__browser__chrome_content_browser_client.cc.patch", "Custom NTP, sandbox mitigation removal"),
            ("fixes/sandbox__policy__linux__bpf_audio_policy_linux.cc.patch", "Allow sched_getaffinity in audio sandbox"),
            ("fixes/third_party__blink__renderer__modules__webaudio__audio_context.cc.patch", "No user gesture required for WebAudio"),
            ("windows/sandbox__win__src__sandbox_policy_base.cc.patch", "Remove MS signed binary enforcement"),
        ],
    },
}

# All categories in order (series ordering)
CATEGORY_ORDER = [
    "thorium-compiler-simd",
    "thorium-build-config",
    "thorium-ui",
    "thorium-privacy",
    "thorium-media-codecs",
    "thorium-quarantine-removal",
    "thorium-extensions-mv3",
    "thorium-dns-network",
    "thorium-downloads",
    "thorium-encryption-machineid",
    "thorium-history-preferences",
    "thorium-telemetry-flags",
    "thorium-gpu-features",
    "thorium-misc-fixes",
    "thorium_branding_append",
]

# Files to REMOVE after merging
REMOVE_PREFIXES = [
    "thorium/config/",
    "thorium/compiler/",
    "thorium/fixes/",
    "thorium/ui/",
    "thorium/features/",
    "thorium/media/",
    "thorium/privacy/",
    "thorium/windows/",
    "thorium/v8/",
]


def read_patch(path):
    """Read a patch file and return its content, preserving CRLF."""
    filepath = THORIUM_DIR / path
    if not filepath.exists():
        print(f"  [WARN] Patch not found: {filepath}")
        return ""
    with open(filepath, "rb") as f:
        data = f.read()
    # Detect if CRLF or LF
    if b"\r\n" in data:
        # Convert CRLF to LF for internal processing, but remember original format
        data = data.replace(b"\r\n", b"\n")
    return data.decode("utf-8")


def ensure_trailing_newline(content):
    """Ensure content ends with a newline."""
    if content and not content.endswith("\n"):
        content += "\n"
    return content


def write_patch(path, content):
    """Write content to a patch file, ensuring CRLF line endings."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # Convert LF to CRLF for Windows patch compatibility
    data = content.encode("utf-8")
    data = data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
    with open(path, "wb") as f:
        f.write(data)
    print(f"  Created: {path.relative_to(BASE)}")


def build_merged_patch(category_info):
    """Build a merged patch from constituent patches."""
    parts = []
    for rel_path, description in category_info["patches"]:
        content = read_patch(rel_path)
        if not content:
            continue
        content = ensure_trailing_newline(content)
        # Add a separator comment for clarity
        header = f"# {description}\n"
        parts.append(header + content)

    if not parts:
        return ""

    full_content = "".join(parts)
    full_content = ensure_trailing_newline(full_content)
    return full_content


def read_existing_patch(path):
    """Read an existing patch file."""
    if path.exists():
        with open(path, "rb") as f:
            return f.read().decode("utf-8")
    return ""


def write_patch(path, content):
    """Write content to a patch file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        f.write(content.encode("utf-8"))
    print(f"  Created: {path.relative_to(BASE)}")


def remove_old_patch_files():
    """Remove old single-file patch directories and their contents."""
    for prefix in REMOVE_PREFIXES:
        dir_path = THORIUM_DIR / prefix
        if dir_path.exists() and dir_path.is_dir():
            shutil.rmtree(dir_path)
            print(f"  Removed: {dir_path.relative_to(BASE)}/")
        elif dir_path.exists() and dir_path.is_file():
            dir_path.unlink()
            print(f"  Removed: {dir_path.relative_to(BASE)}")


def update_series(old_series, categories_info):
    """Update the series file to reflect the new patch organization.

    Strategy: Find the "# diff generated patches" marker and replace
    everything from that point onward with the merged patch entries.
    """
    # Build the order of merged patches
    merged_entries = []
    for cat_key in CATEGORY_ORDER:
        if cat_key in categories_info:
            cat_info = categories_info[cat_key]
            if cat_key == "thorium_branding_append":
                # Branding is merged into the original, so don't add a new entry
                continue
            out_path = cat_info["output"].relative_to(PATCHES_DIR)
            merged_entries.append(f"thorium/{out_path}\n")

    # Find the marker
    marker = "# diff generated patches from original thorium source files"
    marker_idx = old_series.find(marker)

    if marker_idx == -1:
        print("  [WARN] Could not find '# diff generated patches' marker in series!")
        print("  Falling back to filtered approach...")
        return filtered_update(old_series, categories_info, merged_entries)

    # Keep everything up to and including the marker line
    before_marker = old_series[:marker_idx]
    # Find end of the marker line
    eol_idx = old_series.find("\n", marker_idx)
    before_marker = old_series[:eol_idx + 1] if eol_idx != -1 else old_series

    # Build the replacement section
    new_section = []
    new_section.append("# # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # #\n")
    new_section.append("# Merged multi-file patches (reorganized from single-file patches by purpose)\n")
    new_section.append("# # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # #\n")
    new_section.append("\n")
    for entry in merged_entries:
        new_section.append(entry)

    return before_marker + "".join(new_section)


def filtered_update(old_series, categories_info, merged_entries):
    """Fallback: filter line-by-line, removing patches that were merged."""
    lines = old_series.splitlines(keepends=True)

    # Set of all single-file patches that were merged
    all_merged_prefixes = set()
    for cat_key, cat_info in categories_info.items():
        for rel_path, _ in cat_info["patches"]:
            all_merged_prefixes.add(rel_path)

    # Tags to preserve
    keep_prefixes = [
        "thorium/fixes/autogenerated_remove-safebrowsing-prefs-deps.patch",
        "thorium/fixes/remove-safebrowsing-prefs-deps.patch",
        "thorium/fix_upstream/",
        "thorium/original/",
    ]

    def is_merged_patch(line):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            return False
        for prefix in all_merged_prefixes:
            rel = f"thorium/{prefix}"
            if stripped == rel:
                return True
        return False

    def should_keep(line):
        stripped = line.strip()
        if not stripped:
            return True
        for kp in keep_prefixes:
            if stripped.startswith(kp):
                return True
        return False

    new_lines = []
    section_inserted = False
    for line in lines:
        if is_merged_patch(line):
            if not section_inserted:
                new_lines.append("# # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # #\n")
                new_lines.append("# Merged multi-file patches (reorganized from single-file patches)\n")
                new_lines.append("# # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # #\n")
                new_lines.append("\n")
                for entry in merged_entries:
                    new_lines.append(entry)
                new_lines.append("\n")
                section_inserted = True
            continue
        if not should_keep(line) and not line.strip().startswith("#") and line.strip():
            stripped = line.strip()
            # Check if this is an auto-generated patch reference that we somehow missed
            if not is_merged_patch(line):
                new_lines.append(line)
        else:
            new_lines.append(line)

    return "".join(new_lines)


def main():
    print("=" * 70)
    print("Thorium Patch Merger")
    print("Merging single-file patches into multi-file patches by purpose")
    print("=" * 70)

    # Read the current series
    if not SERIES_FILE.exists():
        print(f"ERROR: Series file not found: {SERIES_FILE}")
        return

    with open(SERIES_FILE, "r", encoding="utf-8") as f:
        old_series = f.read()

    # Backup series
    with open(SERIES_BACKUP, "w", encoding="utf-8") as f:
        f.write(old_series)
    print(f"Backup: {SERIES_BACKUP}")

    # Step 1: Build and write merged patches (except branding)
    print("\n[Step 1] Creating merged multi-file patches...")
    branding_content_additions = ""
    for cat_key in CATEGORY_ORDER:
        if cat_key == "thorium_branding_append":
            continue
        cat_info = CATEGORIES[cat_key]
        print(f"\n  Category: {cat_key} ({cat_info['description']})")
        print(f"    Output: {cat_info['output'].relative_to(BASE)}")
        content = build_merged_patch(cat_info)
        if content:
            write_patch(cat_info["output"], content)
            print(f"    Merged {len(cat_info['patches'])} patches")

    # Step 2: Build branding additions and append to existing thorium_branding.patch
    print("\n[Step 2] Merging branding patches into existing thorium_branding.patch...")
    cat_info = CATEGORIES["thorium_branding_append"]
    branding_additions = build_merged_patch(cat_info)
    if branding_additions:
        existing = read_existing_patch(cat_info["output"])
        # Make sure there's a separator between existing and new content
        separator = "\n" if existing.endswith("\n") else "\n\n"
        separator += "# ============================================================\n"
        separator += "# Auto-generated branding patches (merged from single-file)\n"
        separator += "# ============================================================\n\n"
        new_content = existing + separator + branding_additions
        write_patch(cat_info["output"], new_content)
        print(f"    Appended {len(cat_info['patches'])} patches to existing branding patch")

    # Step 3: Update series file
    print("\n[Step 3] Updating series file...")
    new_series = update_series(old_series, CATEGORIES)
    with open(SERIES_FILE, "w", encoding="utf-8") as f:
        f.write(new_series)
    print(f"  Updated: {SERIES_FILE}")

    # Step 4: Remove old patch directories
    print("\n[Step 4] Removing old single-file patch directories...")
    remove_old_patch_files()
    # Also remove the empty merged __init__ marker if present

    print("\n" + "=" * 70)
    print("Done! Summary:")
    for cat_key in CATEGORY_ORDER:
        if cat_key == "thorium_branding_append":
            cat_info = CATEGORIES[cat_key]
            print(f"  {cat_key}: {len(cat_info['patches'])} patches merged into existing thorium_branding.patch")
        else:
            cat_info = CATEGORIES[cat_key]
            print(f"  {cat_key}: {len(cat_info['patches'])} patches -> {cat_info['output'].relative_to(BASE)}")
    print("=" * 70)


if __name__ == "__main__":
    main()
