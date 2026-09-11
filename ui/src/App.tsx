import { Canvas } from '@/components/Canvas'
import { GraphProvider } from '@/contexts/GraphProvider'

export default function App() {
  return (
    <GraphProvider>
      <Canvas />
    </GraphProvider>
  )
}
