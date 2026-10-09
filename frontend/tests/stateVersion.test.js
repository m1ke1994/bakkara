import test from 'node:test'
import assert from 'node:assert/strict'
import { shouldApplySnapshot } from '../src/stateVersion.js'

test('rejects an older API snapshot from the same backend process', () => {
  assert.equal(shouldApplySnapshot(12, 13, 'epoch-a', 'epoch-a'), false)
})

test('accepts a newer snapshot even when an older request returns later', () => {
  assert.equal(shouldApplySnapshot(14, 13, 'epoch-a', 'epoch-a'), true)
})

test('accepts a reset version after the backend process restarts', () => {
  assert.equal(shouldApplySnapshot(1, 99, 'epoch-b', 'epoch-a'), true)
})
