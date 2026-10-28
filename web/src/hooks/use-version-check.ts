"use client";

import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import webConfig from "@/constants/common-env";
import { parseChangelog, type ReleaseInfo } from "@/lib/release";

const latestVersionUrl =
  "https://raw.githubusercontent.com/chenjuanjuan055-ai/chatgpt-pool-ng/main/VERSION";

async function fetchLocalReleases(): Promise<ReleaseInfo[]> {
  const response = await fetch("/CHANGELOG.md", { cache: "no-store" });
  if (!response.ok) throw new Error("changelog unavailable");
  const text = await response.text();
  return parseChangelog(text);
}

function toVersionParts(version: string) {
  const match = version.trim().match(/^v?(\d+)\.(\d+)\.(\d+)/);
  return match ? match.slice(1).map(Number) : null;
}

function isNewerVersion(latestVersion: string, currentVersion: string) {
  const latest = toVersionParts(latestVersion);
  const current = toVersionParts(currentVersion);
  if (!latest || !current) return false;
  return latest.some(
    (value, index) =>
      value > current[index] &&
      latest.slice(0, index).every((part, prevIndex) => part === current[prevIndex]),
  );
}

export function useVersionCheck() {
  const currentVersion = webConfig.appVersion;
  const [localReleases, setLocalReleases] = useState<ReleaseInfo[]>([]);
  const [latestVersion, setLatestVersion] = useState(currentVersion);
  const [releases, setReleases] = useState<ReleaseInfo[]>(localReleases);
  const [checking, setChecking] = useState(false);
  const [open, setOpen] = useState(false);
  const hasNewVersion = isNewerVersion(latestVersion, currentVersion);

  const checkLatestRelease = useCallback(
    async (showMessage = false) => {
      setChecking(true);
      try {
        const local = await fetchLocalReleases().catch(() => []);
        if (local.length) setReleases(local);
        const versionResponse = await fetch(`${latestVersionUrl}?t=${Date.now()}`, { cache: "no-store" });
        if (!versionResponse.ok) throw new Error();
        const version = (await versionResponse.text()).trim();
        setLatestVersion(version || currentVersion);
        if (showMessage) toast.success("已检查最新版本");
      } catch {
        setLatestVersion(currentVersion);
        if (showMessage) toast.error("检查更新失败（不影响本地版本记录）");
      } finally {
        setChecking(false);
      }
    },
    [currentVersion],
  );

  useEffect(() => {
    void fetchLocalReleases()
      .then((items) => {
        if (items.length) setReleases((prev) => (prev.length ? prev : items));
      })
      .catch(() => {});
  }, []);

  const openReleaseModal = () => {
    setOpen(true);
    void checkLatestRelease();
  };

  return {
    open,
    setOpen,
    openReleaseModal,
    latestVersion,
    releases,
    checking,
    hasNewVersion,
    checkLatestRelease,
  };
}
