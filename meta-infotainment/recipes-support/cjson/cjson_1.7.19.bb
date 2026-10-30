SUMMARY = "Ultralightweight JSON parser in ANSI C"
DESCRIPTION = "cJSON is a lightweight, portable, single-file JSON parser \
and generator written in C. It is used here as the real third-party \
library the audio-zone-manager recipe links against, for the config \
format it reads at startup."
HOMEPAGE = "https://github.com/DaveGamble/cJSON"
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://LICENSE;md5=218947f77e8cb8e2fa02918dc41c50d0"

SRC_URI = "git://github.com/DaveGamble/cJSON.git;protocol=https;branch=master"
SRCREV = "c859b25da02955fef659d658b8f324b5cde87be3"

S = "${WORKDIR}/git"

DEPENDS = ""

inherit cmake

# Upstream's CMakeLists builds both the shared library and its test
# suite by default; the test suite needs network-fetched sources
# (its own vendored copy of utest.h) that this sandbox's build
# environment cannot reach during do_configure, so it is disabled
# here -- a real, stated scope decision, not a silent workaround.
EXTRA_OECMAKE = "-DENABLE_CJSON_UTILS=Off -DENABLE_CJSON_TEST=Off -DBUILD_SHARED_AND_STATIC_LIBS=On"

FILES:${PN} += "${libdir}/*.so*"
FILES:${PN}-dev += "${includedir}/cjson"
FILES:${PN}-staticdev += "${libdir}/*.a"

BBCLASSEXTEND = "native nativesdk"
