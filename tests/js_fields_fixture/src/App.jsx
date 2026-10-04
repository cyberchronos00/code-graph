import { Command } from './cmd'

function Panel() { return null }
function Empty() { return null }

export function App({ user, mode }) {
  switch (mode) {
    case 'x':
      return <Panel />
  }
  return <div>{user ? <Panel /> : <Empty />}{user && <Empty />}</div>
}
