export interface DocLink {
  slug: string
  title: string
}

export interface DocGroup {
  title: string
  items: DocLink[]
}

/** Sidebar order and the short labels. Page H1s stay in `docs/`. */
export const docGroups: DocGroup[] = [
  {
    title: 'Start',
    items: [
      { slug: 'quickstart', title: 'Quick start' },
      { slug: 'install', title: 'Install' },
      { slug: 'cli', title: 'CLI' },
      { slug: 'configuration', title: 'Configuration' },
      { slug: 'mcp', title: 'MCP' }
    ]
  },
  {
    title: 'Graph',
    items: [
      { slug: 'architecture', title: 'Architecture' },
      { slug: 'schema', title: 'Schema' },
      { slug: 'completeness', title: 'Completeness' },
      { slug: 'limitations', title: 'Limitations' },
      { slug: 'parity', title: 'Parity' },
      { slug: 'value-facts', title: 'Value facts' },
      { slug: 'plans', title: 'Plans' },
      { slug: 'viz', title: 'Visual view' },
      { slug: 'generated', title: 'Generated' }
    ]
  },
  {
    title: 'Languages',
    items: [
      { slug: 'python', title: 'Python' },
      { slug: 'php', title: 'PHP' },
      { slug: 'kotlin', title: 'Kotlin' },
      { slug: 'swift', title: 'Swift' },
      { slug: 'native', title: 'Rust, C, C++' },
      { slug: 'ts-frameworks', title: 'TS frameworks' }
    ]
  },
  {
    title: 'Boundaries',
    items: [
      { slug: 'platforms', title: 'Platforms' },
      { slug: 'bridges', title: 'Bridges' },
      { slug: 'protocols', title: 'Protocols' },
      { slug: 'external', title: 'External' },
      { slug: 'ai-tools', title: 'AI tools' },
      { slug: 'channels-and-tests', title: 'Channels, tests' }
    ]
  },
  {
    title: 'Reference',
    items: [
      { slug: 'validation', title: 'Validation' },
      { slug: 'validation-log', title: 'Validation log' },
      { slug: 'mcp/sample_outputs', title: 'MCP samples' }
    ]
  }
]

export const docRoutes = docGroups.flatMap(group =>
  group.items.map(item => `/docs/${item.slug}`)
)
