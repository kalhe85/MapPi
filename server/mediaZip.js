// Download a media folder (photos + geo.txt + captures.csv) as a single zip.
// Uses the 'zip' command line tool, which is already a dependency of the deb package.
// Files are stored uncompressed (-0): JPEGs don't compress, and it keeps the
// CPU load low on small boards like the Pi Zero 2 W.

const fs = require('fs')
const path = require('path')
const { spawn } = require('child_process')

// Resolve a user-supplied folder (relative to mediaRoot) to an absolute path.
// Returns null if it is outside mediaRoot or is not an existing directory.
function resolveMediaFolder (mediaRoot, folder) {
  const rel = String(folder || '')
  if (rel.includes('\0')) return null
  const root = path.resolve(mediaRoot)
  const target = path.resolve(root, rel)
  const relative = path.relative(root, target)
  if (relative.startsWith('..') || path.isAbsolute(relative)) return null
  try {
    if (!fs.statSync(target).isDirectory()) return null
  } catch {
    return null
  }
  return target
}

// File name for the downloaded zip, e.g. "rpanion_media_flight_01.zip"
function zipName (mediaRoot, target) {
  const rel = path.relative(path.resolve(mediaRoot), target)
  const label = rel === '' ? 'all' : rel.replace(/[^A-Za-z0-9_-]+/g, '_')
  return `rpanion_media_${label}.zip`
}

// Stream the zip of 'target' into the HTTP response
function sendFolderZip (mediaRoot, target, res, zipBin = 'zip') {
  const zip = spawn(zipBin, ['-r', '-0', '-q', '-', '.'], { cwd: target })
  let finished = false

  const fail = (message) => {
    if (finished) return
    finished = true
    console.error('Media zip failed:', message)
    if (!res.headersSent) {
      res.status(500).json({ error: message })
    } else {
      // zip data was already being sent - abort so the client sees an error
      res.destroy(new Error(message))
    }
  }

  // Only send headers once zip actually produces data, so errors can still be reported
  zip.stdout.on('data', (chunk) => {
    if (!res.headersSent) {
      res.setHeader('Content-Type', 'application/zip')
      res.setHeader('Content-Disposition', `attachment; filename="${zipName(mediaRoot, target)}"`)
    }
    if (!res.write(chunk)) {
      zip.stdout.pause()
      res.once('drain', () => zip.stdout.resume())
    }
  })
  zip.on('error', (err) => fail(`zip could not be run: ${err.message}`))
  zip.on('close', (code) => {
    if (code === 0) {
      if (!finished) {
        finished = true
        res.end()
      }
    } else {
      // e.g. code 12 = "nothing to do" (empty folder)
      fail(`zip exited with code ${code}`)
    }
  })
  // Stop zipping if the client disconnects
  res.on('close', () => {
    if (zip.exitCode === null) zip.kill()
  })
}

module.exports = { resolveMediaFolder, zipName, sendFolderZip }
