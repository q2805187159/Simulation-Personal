#!/bin/sh
set -eu

destination=${1:?Usage: sh build.sh OUTPUT_DIRECTORY [OPENMPP_ARCHIVE]}
model_source=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
build_dir=$(mktemp -d)
trap 'rm -rf "$build_dir"' EXIT

archive_name=openmpp_debian-12_20261001.tar.gz
archive_hash=1964c5297bf87fb6fcb1436854ed5fa76bafa55bd69326b9be526716ab60c8bc
if [ "$#" -ge 2 ]; then
    cp -- "$2" "$build_dir/$archive_name"
else
    curl --fail --location --silent --show-error --retry 3 \
        "https://github.com/openmpp/main/releases/download/v1.17.15/$archive_name" \
        --output "$build_dir/$archive_name"
fi

(
    cd "$build_dir"
    printf '%s  %s\n' "$archive_hash" "$archive_name" | sha256sum --check -
    tar -xzf "$archive_name"
)

sdk="$build_dir/${archive_name%.tar.gz}"
mkdir "$build_dir/model"
cp -R "$model_source/code" "$model_source/parameters" "$model_source/makefile" "$build_dir/model/"
# OpenM++'s makefile generates multiple files in one recipe and needs a serial build.
make -j1 -C "$build_dir/model" RELEASE=1 OM_ROOT="$sdk"

install -d "$destination"
install -m 0755 "$build_dir/model/ompp-linux/bin/SkeletonCase" "$destination/"
install -m 0644 "$build_dir/model/ompp-linux/build/release/src/SkeletonCase.sqlite" "$destination/"
install -m 0644 "$sdk/LICENSE.txt" "$destination/LICENSE.OpenMpp.txt"
