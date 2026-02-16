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
          pythonEnv = pkgs.python312.withPackages (ps: [
            ps.pandas
            ps.titlecase
            ps.numpy
            ps.fastapi
            ps.python-multipart
            ps.uvicorn
            ps.httpx
            ps.pillow
            ps.beancount_2
          ]);
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
            export PYTHONPATH="$bb_pythonpath"
          '';
          bb = pkgs.writeShellScriptBin "bb" ''
            ${pythonPathSetup}
            exec ${pythonEnv}/bin/python -m beanbeaver.cli.main "$@"
          '';
        in {
            devShells.default = pkgs.mkShell {
                buildInputs = [
                  bb
                  pkgs.fava

                  pythonEnv

                  # Linting and type checking
                  pkgs.ruff
                  pkgs.mypy
                ];
                shellHook = ''
                  ${pythonPathSetup}
                  export PATH="${pythonEnv}/bin:$PATH"
                '';
            };
        }
    );

}
