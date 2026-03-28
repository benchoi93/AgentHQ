import type { SpriteData } from '../types'
import { setCharacterTemplates } from './spriteData'
import { setWallSprites } from '../wallTiles'

const CHAR_COUNT = 6
const CHAR_FRAME_W = 16
const CHAR_FRAME_H = 32
const FRAMES_PER_ROW = 7
const DIRECTIONS = ['down', 'up', 'right'] as const
const ALPHA_THRESHOLD = 128

function pngToSpriteData(
  data: Uint8ClampedArray,
  imgWidth: number,
  frameX: number,
  frameY: number,
  frameW: number,
  frameH: number,
): SpriteData {
  const sprite: string[][] = []
  for (let y = 0; y < frameH; y++) {
    const row: string[] = []
    for (let x = 0; x < frameW; x++) {
      const idx = ((frameY + y) * imgWidth + (frameX + x)) * 4
      const r = data[idx]
      const g = data[idx + 1]
      const b = data[idx + 2]
      const a = data[idx + 3]
      if (a < ALPHA_THRESHOLD) {
        row.push('')
      } else {
        row.push(
          '#' +
            r.toString(16).padStart(2, '0') +
            g.toString(16).padStart(2, '0') +
            b.toString(16).padStart(2, '0'),
        )
      }
    }
    sprite.push(row)
  }
  return sprite
}

function loadImage(src: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new Image()
    img.onload = () => resolve(img)
    img.onerror = () => reject(new Error(`Failed to load ${src}`))
    img.src = src
  })
}

function getImageData(img: HTMLImageElement): ImageData {
  const canvas = document.createElement('canvas')
  canvas.width = img.width
  canvas.height = img.height
  const ctx = canvas.getContext('2d')!
  ctx.drawImage(img, 0, 0)
  return ctx.getImageData(0, 0, img.width, img.height)
}

interface CharacterSpriteSet {
  down: SpriteData[]
  up: SpriteData[]
  right: SpriteData[]
}

async function loadCharacterSprites(): Promise<void> {
  const characters: CharacterSpriteSet[] = []

  for (let ci = 0; ci < CHAR_COUNT; ci++) {
    try {
      const img = await loadImage(`/assets/characters/char_${ci}.png`)
      const imageData = getImageData(img)
      const data = imageData.data

      const charData: CharacterSpriteSet = { down: [], up: [], right: [] }

      for (let dirIdx = 0; dirIdx < DIRECTIONS.length; dirIdx++) {
        const dir = DIRECTIONS[dirIdx]
        const rowOffsetY = dirIdx * CHAR_FRAME_H
        const frames: SpriteData[] = []

        for (let f = 0; f < FRAMES_PER_ROW; f++) {
          frames.push(
            pngToSpriteData(data, img.width, f * CHAR_FRAME_W, rowOffsetY, CHAR_FRAME_W, CHAR_FRAME_H),
          )
        }
        charData[dir] = frames
      }
      characters.push(charData)
    } catch {
      // Skip failed loads — fallback palette templates will be used
      console.warn(`Failed to load char_${ci}.png, using fallback palette`)
    }
  }

  if (characters.length > 0) {
    setCharacterTemplates(characters)
  }
}

const WALL_TILE_W = 16
const WALL_TILE_H = 32
const WALL_COLS = 4

async function loadWallSprites(): Promise<void> {
  try {
    const img = await loadImage('/assets/walls.png')
    const imageData = getImageData(img)
    const data = imageData.data
    const rows = Math.ceil(16 / WALL_COLS)
    const sprites: SpriteData[] = []

    for (let r = 0; r < rows; r++) {
      for (let c = 0; c < WALL_COLS; c++) {
        const idx = r * WALL_COLS + c
        if (idx >= 16) break
        sprites.push(
          pngToSpriteData(data, img.width, c * WALL_TILE_W, r * WALL_TILE_H, WALL_TILE_W, WALL_TILE_H),
        )
      }
    }

    if (sprites.length === 16) {
      setWallSprites(sprites)
    }
  } catch {
    console.warn('Failed to load walls.png, using solid wall colors')
  }
}

/** Load all PNG sprite assets. Safe to call multiple times. */
export async function loadAllSprites(): Promise<void> {
  await Promise.all([loadCharacterSprites(), loadWallSprites()])
}
