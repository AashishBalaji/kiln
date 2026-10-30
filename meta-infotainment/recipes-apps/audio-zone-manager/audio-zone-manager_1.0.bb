SUMMARY = "Infotainment audio zone control daemon"
DESCRIPTION = "A small daemon that owns the set of audio zones (front, \
rear, trunk, ...) in an infotainment system, loading their default \
volume/mute state from a JSON config at startup (parsed with cJSON) and \
exposing a tiny Unix-domain-socket control protocol (GET/SET/LIST) for \
other processes such as a HMI or diagnostics tool. Demonstrates a \
custom service recipe depending on a third-party library recipe in \
this same layer, with a systemd unit and a config file installed to \
their standard locations."
HOMEPAGE = "https://example.invalid/kiln/audio-zone-manager"
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"

SRC_URI = "file://main.c \
           file://CMakeLists.txt \
           file://audio-zone-manager.service \
           file://zones.json"

S = "${WORKDIR}"

DEPENDS = "cjson"

inherit cmake systemd

SYSTEMD_SERVICE:${PN} = "audio-zone-manager.service"
SYSTEMD_AUTO_ENABLE = "enable"

do_install:append() {
    install -d ${D}${systemd_system_unitdir}
    install -m 0644 ${WORKDIR}/audio-zone-manager.service ${D}${systemd_system_unitdir}/audio-zone-manager.service

    install -d ${D}${sysconfdir}/audio-zone-manager
    install -m 0644 ${WORKDIR}/zones.json ${D}${sysconfdir}/audio-zone-manager/zones.json
}

FILES:${PN} += "${systemd_system_unitdir}/audio-zone-manager.service \
                ${sysconfdir}/audio-zone-manager/zones.json"

CONFFILES:${PN} += "${sysconfdir}/audio-zone-manager/zones.json"
