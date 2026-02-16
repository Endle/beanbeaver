{
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    #nixpkgs.url = "github:nixos/nixpkgs/nixos-24.11";
    flake-utils.url = "github:numtide/flake-utils";
  };
  outputs = { self, nixpkgs, flake-utils}:
    flake-utils.lib.eachDefaultSystem (
      system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          bb = pkgs.writeShellScriptBin "bb" ''
            export PYTHONPATH="$PWD/vendor''${PYTHONPATH:+:$PYTHONPATH}"
            exec python -m beanbeaver.cli "$@"
          '';
        in {
            devShells.default = pkgs.mkShell {
                buildInputs = [
                  bb
                  pkgs.beancount
                  pkgs.fava

                  pkgs.python312
                  pkgs.python312Packages.pandas
                  pkgs.python312Packages.titlecase
                  pkgs.python312Packages.numpy
                  pkgs.python312Packages.fastapi
                  pkgs.python312Packages.python-multipart
                  pkgs.python312Packages.uvicorn
                  pkgs.python312Packages.httpx
                  pkgs.python312Packages.pillow
                  pkgs.python312Packages.beancount

                  # Linting and type checking
                  pkgs.ruff
                  pkgs.mypy
                ];
                shellHook = ''
                  export PYTHONPATH="$PWD/vendor''${PYTHONPATH:+:$PYTHONPATH}"
                '';
            };
        }
    );

}
