import { useState, type FormEvent } from 'react'

import type { CanvasDrawerProps } from '@/types'

export function CanvasDrawer({
  canvases,
  selectedCanvasId,
  isBusy,
  onSelect,
  onRename,
  onDelete,
}: CanvasDrawerProps) {
  const selectedCanvas = canvases.find(
    (canvas) => canvas.id === selectedCanvasId,
  )
  const [name, setName] = useState(selectedCanvas?.name ?? '')

  function handleRename(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (selectedCanvasId && name.trim()) {
      void onRename(selectedCanvasId, name.trim())
    }
  }

  return (
    <nav aria-label="Canvas switcher">
      <details>
        <summary>Canvases</summary>
        <label>
          Open canvas
          <select
            value={selectedCanvasId ?? ''}
            onChange={(event) => onSelect(event.target.value || undefined)}
            disabled={isBusy}
          >
            <option value="">New canvas</option>
            {canvases.map((canvas) => (
              <option key={canvas.id} value={canvas.id}>
                {canvas.name}
              </option>
            ))}
          </select>
        </label>
        {selectedCanvasId ? (
          <form aria-label="Rename or delete canvas" onSubmit={handleRename}>
            <label>
              Canvas name
              <input
                value={name}
                onChange={(event) => setName(event.target.value)}
                required
              />
            </label>
            <button type="submit" disabled={isBusy}>Rename</button>
            <button
              type="button"
              disabled={isBusy}
              onClick={() => void onDelete(selectedCanvasId)}
            >
              Delete
            </button>
          </form>
        ) : null}
      </details>
    </nav>
  )
}
