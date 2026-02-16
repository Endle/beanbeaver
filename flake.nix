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
          pythonPathSetup = ''
            bb_pythonpath=""
            if [ -d "$PWD/vendor/beanbeaver" ]; then
              bb_pythonpath="$PWD/vendor"
            elif [ -d "$PWD/beanbeaver" ]; then
              bb_pythonpath="$PWD"
            elif [ -d "$PWD/../beanbeaver" ]; then
              bb_pythonpath="$PWD/.."
            else
              echo "Could not resolve beanbeaver PYTHONPATH from: $PWD" >&2
              echo "Run from project root, vendor/, or vendor/beanbeaver." >&2
              exit 1
            fi
            export PYTHONPATH="$bb_pythonpath''${PYTHONPATH:+:$PYTHONPATH}"
          '';
          bb = pkgs.writeShellScriptBin "bb" ''
            ${pythonPathSetup}
            exec ${pkgs.python312}/bin/python -m beanbeaver.cli.main "$@"
          '';
        in {
            devShells.default = pkgs.mkShell {
                buildInputs = [
                  bb
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
                  ${pythonPathSetup}
                  export PATH="${pkgs.python312}/bin:$PATH"
                '';
            };
        }
    );

}
