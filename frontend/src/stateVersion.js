export function shouldApplySnapshot(nextVersion, currentVersion, nextEpoch, currentEpoch) {
  if (nextEpoch && currentEpoch && nextEpoch !== currentEpoch) return true
  return Number(nextVersion ?? 0) >= Number(currentVersion ?? 0)
}
