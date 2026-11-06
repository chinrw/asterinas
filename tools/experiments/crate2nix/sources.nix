# SPDX-License-Identifier: MPL-2.0
{ lib, root }:
let
  members = builtins.fromJSON (builtins.readFile ./generated/source-map.json);
  sourceFor =
    name: relative:
    let
      crateRoot = root + "/${relative}";
      children = builtins.filter (path: lib.hasPrefix "${relative}/" path) (builtins.attrValues members);
    in
    builtins.path {
      path = crateRoot;
      name = "source-${name}";
      filter =
        path: type:
        let
          local = lib.removePrefix "${toString root}/" (toString path);
        in
        !(builtins.elem local children)
        && !(builtins.elem (baseNameOf path) [
          ".git"
          "target"
        ]);
    };
in
builtins.mapAttrs sourceFor members
