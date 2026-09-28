# SPDX-License-Identifier: MPL-2.0

# Let every Nix command started by Make pull from the Cachix caches declared in
# flake.nix. Set ASTER_NIX_CACHES=0 to leave them out, for example when Nix
# warns that an untrusted user cannot add substituters. Without Nix the
# settings stay empty, so targets that do not use Nix still work.
ASTER_NIX_CACHES ?= 1

# Read the settings once per make tree. A sub-make that includes this file
# again would otherwise repeat them.
ifndef ASTER_NIX_MK_DONE
ASTER_NIX_MK_DONE := 1
ASTER_NIX_CONFIG :=
ifeq ($(ASTER_NIX_CACHES),1)
ifneq ($(shell command -v nix-instantiate),)
ASTER_FLAKE_NIX_CONFIG := (import $(abspath $(dir $(lastword $(MAKEFILE_LIST)))../../flake.nix)).nixConfig
# Use `:=` so that each value is evaluated here once. The root Makefile exports
# every variable, and Make would expand a deferred one again for each recipe.
# `nix-instantiate --eval` prints a quoted string. Its --raw flag would drop the
# quotes but needs Nix 2.25 or later, so the quotes are removed here instead.
ASTER_SUBSTITUTERS := $(subst ",,$(shell nix-instantiate --eval -E 'toString $(ASTER_FLAKE_NIX_CONFIG).extra-substituters' 2>/dev/null))
ASTER_TRUSTED_PUBLIC_KEYS := $(subst ",,$(shell nix-instantiate --eval -E 'toString $(ASTER_FLAKE_NIX_CONFIG).extra-trusted-public-keys' 2>/dev/null))
ifeq ($(ASTER_SUBSTITUTERS),)
$(warning Cannot read the Cachix settings from flake.nix, so Nix commands will not use those caches)
else
define ASTER_NIX_CONFIG :=
extra-substituters = $(ASTER_SUBSTITUTERS)
extra-trusted-public-keys = $(ASTER_TRUSTED_PUBLIC_KEYS)
endef
endif
endif
endif
ifneq ($(origin NIX_CONFIG),command line)
# Keep the caller's own settings and add ours after them.
define NIX_CONFIG :=
$(NIX_CONFIG)
$(ASTER_NIX_CONFIG)
endef
endif
export ASTER_NIX_MK_DONE ASTER_NIX_CONFIG
endif

# A NIX_CONFIG given on the command line overrides the assignment above and
# reaches every sub-make through MAKEFLAGS, so extend it at every level.
ifeq ($(origin NIX_CONFIG),command line)
override define NIX_CONFIG :=
$(NIX_CONFIG)
$(ASTER_NIX_CONFIG)
endef
endif
export NIX_CONFIG
