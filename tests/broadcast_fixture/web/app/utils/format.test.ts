import { describe, expect, it } from 'vitest'
import { taskLabel } from './format'

describe('taskLabel', () => {
  it('adds the state', () => {
    expect(taskLabel('Write docs', 'todo')).toBe('Write docs (todo)')
  })
})
