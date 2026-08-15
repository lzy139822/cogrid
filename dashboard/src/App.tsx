import { Routes, Route } from 'react-router-dom'
import Layout from './components/Layout'
import Overview from './pages/Overview'
import Nodes from './pages/Nodes'
import Leaderboard from './pages/Leaderboard'
import Tasks from './pages/Tasks'
import SubmitTask from './pages/SubmitTask'

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Layout />}>
        <Route index element={<Overview />} />
        <Route path="nodes" element={<Nodes />} />
        <Route path="leaderboard" element={<Leaderboard />} />
        <Route path="tasks" element={<Tasks />} />
        <Route path="submit" element={<SubmitTask />} />
      </Route>
    </Routes>
  )
}
