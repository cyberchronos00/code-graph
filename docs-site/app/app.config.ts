export default defineAppConfig({
  ui: {
    colors: {
      primary: 'lime',
      secondary: 'violet',
      neutral: 'zinc'
    }
  },
  seo: {
    siteName: 'code-graph'
  },
  header: {
    title: 'code-graph',
    to: '/',
    search: true,
    colorMode: true,
    links: [{
      icon: 'i-simple-icons-github',
      to: 'https://github.com/cyberchronos00/code-graph',
      target: '_blank',
      'aria-label': 'code-graph on GitHub'
    }]
  },
  toc: {
    title: 'On this page',
    bottom: {
      title: 'Source',
      edit: 'https://github.com/cyberchronos00/code-graph/edit/main/docs',
      links: [{
        icon: 'i-lucide-star',
        label: 'Star on GitHub',
        to: 'https://github.com/cyberchronos00/code-graph',
        target: '_blank'
      }]
    }
  }
})
