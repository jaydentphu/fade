---
name: Fade Liquid Glass
colors:
  surface: '#171210'
  surface-dim: '#171210'
  surface-bright: '#3d3836'
  surface-container-lowest: '#110d0b'
  surface-container-low: '#1f1b18'
  surface-container: '#231f1c'
  surface-container-high: '#2e2927'
  surface-container-highest: '#393431'
  on-surface: '#eae0dd'
  on-surface-variant: '#e0c0b6'
  inverse-surface: '#eae0dd'
  inverse-on-surface: '#342f2d'
  outline: '#a78a82'
  outline-variant: '#58423b'
  surface-tint: '#ffb59e'
  primary: '#ffb59e'
  on-primary: '#5e1700'
  primary-container: '#ec683d'
  on-primary-container: '#521300'
  inverse-primary: '#a8380f'
  secondary: '#ffb95d'
  on-secondary: '#462a00'
  secondary-container: '#d08600'
  on-secondary-container: '#442800'
  tertiary: '#6ed2fe'
  on-tertiary: '#003547'
  tertiary-container: '#299bc5'
  on-tertiary-container: '#002e3e'
  error: '#ffb4ab'
  on-error: '#690005'
  error-container: '#93000a'
  on-error-container: '#ffdad6'
  primary-fixed: '#ffdbd0'
  primary-fixed-dim: '#ffb59e'
  on-primary-fixed: '#3a0b00'
  on-primary-fixed-variant: '#852400'
  secondary-fixed: '#ffddb7'
  secondary-fixed-dim: '#ffb95d'
  on-secondary-fixed: '#2a1700'
  on-secondary-fixed-variant: '#653e00'
  tertiary-fixed: '#bfe9ff'
  tertiary-fixed-dim: '#6ed2fe'
  on-tertiary-fixed: '#001f2a'
  on-tertiary-fixed-variant: '#004d65'
  background: '#171210'
  on-background: '#eae0dd'
  surface-variant: '#393431'
  win-state: '#639922'
  loss-fade: '#A32D2D'
  text-primary: '#F2EFEA'
  text-muted: '#8A8580'
  surface-border: '#2A2422'
  surface-glass: rgba(20, 16, 14, 0.8)
typography:
  headline-lg:
    fontFamily: Geist
    fontSize: 24px
    fontWeight: '600'
    lineHeight: 32px
    letterSpacing: -0.02em
  headline-md:
    fontFamily: Geist
    fontSize: 18px
    fontWeight: '600'
    lineHeight: 24px
    letterSpacing: -0.01em
  body-lg:
    fontFamily: Geist
    fontSize: 15px
    fontWeight: '500'
    lineHeight: 22px
    letterSpacing: 0em
  body-md:
    fontFamily: Geist
    fontSize: 14px
    fontWeight: '400'
    lineHeight: 20px
    letterSpacing: 0em
  body-sm:
    fontFamily: Geist
    fontSize: 13px
    fontWeight: '400'
    lineHeight: 18px
    letterSpacing: 0.01em
  label-sm:
    fontFamily: Geist
    fontSize: 11px
    fontWeight: '500'
    lineHeight: 14px
    letterSpacing: 0.05em
  headline-lg-mobile:
    fontFamily: Geist
    fontSize: 20px
    fontWeight: '600'
    lineHeight: 28px
rounded:
  sm: 0.25rem
  DEFAULT: 0.5rem
  md: 0.75rem
  lg: 1rem
  xl: 1.5rem
  full: 9999px
spacing:
  space-xs: 4px
  space-sm: 8px
  space-md: 12px
  space-lg: 14px
  space-xl: 24px
  gutter: 12px
  margin-mobile: 16px
  margin-desktop: 24px
---

## Brand & Style

The design system embodies a "Liquid Glass" aesthetic—a sophisticated blend of high-fidelity financial tooling and competitive gaming culture. It is designed to feel premium, dark, and highly technical, yet maintain a tactile, organic quality through the use of translucency and "blood" or "earth" tones.

The visual style is defined by **Minimalist Glassmorphism**. It utilizes near-black surfaces with subtle transparency, high-gloss finishes, and hairline borders to create a sense of depth and precision. The emotional response should be one of focused intensity, professional reliability, and high-stakes excitement. This system is tailored for high-density information environments where clarity and mood are equally paramount.

## Colors

The palette is anchored in a deep, atmospheric **Near-black (#14100E)** which serves as the canvas for all interactions. The core identity is driven by **Blood Orange**, used for primary actions and active positions, providing a high-contrast but grounded focal point.

Semantic colors deviate from standard digital palettes to match the "Liquid" theme:
- **Win State**: An organic Acid Green replaces neon variants for a more sophisticated achievement feel.
- **Loss State**: A Deep Red (Cinnabar) represents "fading" or liquidation.
- **Gold Accent**: Amber is reserved for prestige moments, such as leaderboards and trophy milestones.
- **Typography**: Uses Off-white for maximum legibility and Warm Gray for secondary metadata to maintain the earthy, dark-mode warmth.

## Typography

This design system uses **Geist** exclusively to leverage its technical, developer-centric precision and clean geometry. The typographic hierarchy is optimized for high-density data, favoring smaller font sizes with generous weights to maintain readability.

Key principles:
- **Tight Tracking**: Larger headings use slight negative letter-spacing for a "compacted" professional look.
- **Metadata Labels**: Smallest text levels (11px) use increased tracking and medium weights to ensure clarity on dark glass backgrounds.
- **Contrast**: Content hierarchy is established through weight shifts (Medium to Semibold) rather than massive size differentials.

## Layout & Spacing

The layout follows a **Fixed Grid** philosophy within its containers, specifically optimized for a 380px–420px "Feed" width, though it scales to fluid layouts on larger screens. 

- **Grid System**: A 12-column grid is used for desktop layouts, but the primary logic is based on internal container padding.
- **The 14px Rule**: A consistent 14px padding is applied to all cards and major content blocks to create a rhythmic, structured appearance.
- **Density**: Gaps are kept tight (8px-12px) to allow for high information density without sacrificing visual breathing room.
- **Responsiveness**: On mobile, outer margins shrink to 16px, and internal card gaps tighten to 10px.

## Elevation & Depth

Hierarchy is achieved through **Glassmorphism** and tonal layering rather than traditional heavy shadows.

- **Surface Layers**: The base background is solid near-black. Floating cards use a semi-transparent glass effect (80% opacity) with a `backdrop-filter: blur(12px)`.
- **Hairline Borders**: Depth is defined by 0.5px or 1px solid borders (`#2A2422`). These borders catch "light" and define the edges of the liquid glass containers.
- **Inner Glow**: Buttons and primary cards feature a very subtle internal top-border (0.5px) that is slightly lighter than the surface color to simulate a glass edge.
- **Shadows**: Shadows are rarely used, but when necessary, they are highly diffused, low-opacity (#000000, 40%) with no spread, appearing as a soft ambient occlusion.

## Shapes

The shape language is defined by modern, medium roundedness that feels "smooth" like liquid but retains structural integrity.

- **Cards/Modules**: Use a 14px radius. 
- **Buttons**: Use an 8px radius to feel distinct from the containers they sit within.
- **Avatars**: Always circular (50% radius).
- **Nested Elements**: This design system utilizes a specific nested logic where internal elements (cards) may have a slightly larger radius (14px) than the outermost container (12px), creating a unique "held" visual effect.

## Components

### Buttons
- **Primary**: Solid Blood Orange background with Off-white text. High-gloss finish.
- **Secondary**: Glass background with 0.5px Warm Gray border.
- **Semantic**: "Yes/No" actions use muted variants of Green and Red with matching hairline borders and low-opacity backgrounds.

### Cards
- **Liquid Glass**: Card containers must use the `#14100E` color at 80% opacity with a 12px backdrop blur. 
- **Borders**: Always use a 0.5px border in `#2A2422` for standard cards.

### Input Fields
- **Style**: Dark, recessed appearance. 1px border that glows Blood Orange on focus. 
- **Typography**: Text entry should be Off-white, placeholder text in Warm Gray.

### Chips & Badges
- **Status Chips**: Small, 8px radius, using semantic background tints (e.g., 10% opacity Acid Green for "Win").
- **Icons**: Use Tabler Icons at 15px-16px, inheriting the color of the adjacent text.

### Progress Bars
- **Liquid Fill**: Success states use Acid Green; fading states use Deep Red. The track is always the near-black background color with a 1px recessed border.