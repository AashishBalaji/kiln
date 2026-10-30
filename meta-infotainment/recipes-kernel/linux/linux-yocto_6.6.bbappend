# Extends the real linux-yocto_6.6 recipe in poky/meta with:
#   - a real kernel source patch (a genuine driver bug found and fixed
#     against the actual upstream sound/soc/codecs/tlv320aic31xx.c,
#     verified with `git apply --check` against a pristine checkout --
#     see docs/validation-notes.md for how)
#   - a kernel config fragment enabling the audio subsystem this
#     infotainment layer's codec driver and simple-card machine driver
#     need, with every symbol name verified against this kernel
#     version's real Kconfig stanzas rather than guessed
#
# This is the standard Yocto BSP pattern for carrying board/product
# -specific kernel changes without forking the whole kernel recipe.

FILESEXTRAPATHS:prepend := "${THISDIR}/linux-yocto:"

SRC_URI += "file://0001-ASoC-tlv320aic31xx-fix-swallowed-reset-failure-on-p.patch \
            file://infotainment-audio.cfg"
