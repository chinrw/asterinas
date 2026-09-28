# SPDX-License-Identifier: MPL-2.0

# Let every Nix command started by Make pull from the Cachix caches declared in
# flake.nix. On hosts without Nix the settings stay empty, so targets that do
# not use Nix still work there.
ifndef ASTER_NIX_CONFIG
ASTER_FLAKE_NIX_CONFIG := (import $(abspath $(dir $(lastword $(MAKEFILE_LIST)))../../flake.nix)).nixConfig
# Evaluate each setting once here. The root Makefile exports every variable,
# and Make would expand a deferred variable again for each recipe.
ASTER_NIX_EVAL := command -v nix-instantiate >/dev/null && nix-instantiate --eval --raw -E
ASTER_SUBSTITUTERS := $(shell $(ASTER_NIX_EVAL) 'toString $(ASTER_FLAKE_NIX_CONFIG).extra-substituters')
ASTER_TRUSTED_PUBLIC_KEYS := $(shell $(ASTER_NIX_EVAL) 'toString $(ASTER_FLAKE_NIX_CONFIG).extra-trusted-public-keys')
define ASTER_NIX_CONFIG :=
extra-substituters = $(ASTER_SUBSTITUTERS)
extra-trusted-public-keys = $(ASTER_TRUSTED_PUBLIC_KEYS)
endef
# Keep the caller's own settings and add ours after them.
define NIX_CONFIG :=
$(NIX_CONFIG)
$(ASTER_NIX_CONFIG)
endef
export ASTER_NIX_CONFIG NIX_CONFIG
endif
