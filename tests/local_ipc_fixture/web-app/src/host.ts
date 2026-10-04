const TRUSTED = 'https://embed.example.com'

window.addEventListener('message', (event) => {
  if (event.origin !== TRUSTED) return
  switch (event.data.type) {
    case 'resize':
      document.body.style.height = event.data.height
      break
    case 'close':
      document.body.remove()
      break
  }
})
