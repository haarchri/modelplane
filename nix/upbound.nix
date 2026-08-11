# The Upbound CLI (up and docker-credential-up). nixpkgs' upbound package
# pins a sources file that lags the CDN's stable channel - the up source
# repository is private, so nixpkgs can't auto-update it. This overlay
# replaces it with the release binaries from the same CDN nixpkgs uses.
#
# To bump: set version to what https://cli.upbound.io/stable/current/version
# reports and refresh the hashes:
#
#   for p in linux_amd64 linux_arm64 darwin_arm64; do
#     for b in up docker-credential-up; do
#       curl -sL "https://cli.upbound.io/stable/v<version>/bin/$p/$b" | sha256sum
#     done
#   done
_final: prev:
let
  version = "0.53.1";

  hashes = {
    linux_amd64 = {
      up = "8a701cf15aefa4d35605b329f7cb8e442d4f74cc2c9aa14c1ac020b2ca5cc4e2";
      docker-credential-up = "1ef5440465ae8e3afdc93814ad469d84824ffa7e960dd17192dadaae56bb7e51";
    };
    linux_arm64 = {
      up = "a4dce7704128db478eba06864cf50b7ef5755a754480a7bf72d0e7eeb3767207";
      docker-credential-up = "9f58d4a31a3627a96118dd8f9620e28e5493bedc587720e378aa3059a68834cb";
    };
    darwin_arm64 = {
      up = "12af822e6a2e172b0916056597921280b0c195bae58e06f0946d7c60c0e34a37";
      docker-credential-up = "30c02d248edc0fc78f599c4126c91fcf331b9c24927ccd5ec930c96689fe626c";
    };
  };

  platform =
    {
      x86_64-linux = "linux_amd64";
      aarch64-linux = "linux_arm64";
      aarch64-darwin = "darwin_arm64";
    }
    .${prev.stdenv.hostPlatform.system};

  fetchBin =
    name:
    prev.fetchurl {
      inherit name;
      url = "https://cli.upbound.io/stable/v${version}/bin/${platform}/${name}";
      sha256 = hashes.${platform}.${name};
    };
in
{
  upbound = prev.stdenvNoCC.mkDerivation {
    pname = "upbound";
    inherit version;

    dontUnpack = true;

    installPhase = ''
      runHook preInstall
      install -Dm755 ${fetchBin "up"} $out/bin/up
      install -Dm755 ${fetchBin "docker-credential-up"} $out/bin/docker-credential-up
      runHook postInstall
    '';

    meta = {
      description = "CLI for interacting with Upbound";
      homepage = "https://docs.upbound.io/cli/";
      license = prev.lib.licenses.unfree;
      mainProgram = "up";
    };
  };
}
