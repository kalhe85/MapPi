const assert = require('assert')
const fs = require('fs')
const os = require('os')
const path = require('path')
const { PassThrough } = require('stream')
const { spawnSync } = require('child_process')
const mediaZip = require('./mediaZip')

// Minimal stand-in for an Express response
function mockResponse () {
  const res = new PassThrough()
  const chunks = []
  res.on('data', (c) => chunks.push(c))
  res.headers = {}
  res.statusCode = 200
  res.headersSent = false
  res.setHeader = (k, v) => { res.headers[k.toLowerCase()] = v }
  res.status = (code) => { res.statusCode = code; return res }
  res.json = (obj) => { res.body = obj; res.headersSent = true; res.end(); return res }
  const origWrite = res.write.bind(res)
  res.write = (chunk) => { res.headersSent = true; return origWrite(chunk) }
  res.getBody = () => Buffer.concat(chunks)
  return res
}

describe('Media zip download', function () {
  let root

  beforeEach(function () {
    root = fs.mkdtempSync(path.join(os.tmpdir(), 'rpanion-media-'))
    fs.mkdirSync(path.join(root, 'flight_01'))
    fs.writeFileSync(path.join(root, 'flight_01', 'a.jpg'), 'jpegdata')
    fs.writeFileSync(path.join(root, 'flight_01', 'geo.txt'), 'EPSG:4326\n')
    fs.mkdirSync(path.join(root, 'empty'))
  })

  afterEach(function () {
    fs.rmSync(root, { recursive: true, force: true })
  })

  it('#resolveMediaFolder() stays inside the media folder', function () {
    assert.equal(mediaZip.resolveMediaFolder(root, ''), path.resolve(root))
    assert.equal(mediaZip.resolveMediaFolder(root, undefined), path.resolve(root))
    assert.equal(mediaZip.resolveMediaFolder(root, 'flight_01'), path.join(path.resolve(root), 'flight_01'))
    assert.equal(mediaZip.resolveMediaFolder(root, '../'), null)
    assert.equal(mediaZip.resolveMediaFolder(root, 'flight_01/../../etc'), null)
    assert.equal(mediaZip.resolveMediaFolder(root, '/etc'), null)
    assert.equal(mediaZip.resolveMediaFolder(root, 'missing'), null)
    assert.equal(mediaZip.resolveMediaFolder(root, 'flight_01/a.jpg'), null)
    assert.equal(mediaZip.resolveMediaFolder(root, 'a\0b'), null)
  })

  it('#zipName()', function () {
    assert.equal(mediaZip.zipName(root, path.resolve(root)), 'rpanion_media_all.zip')
    assert.equal(mediaZip.zipName(root, path.join(root, 'flight 01')), 'rpanion_media_flight_01.zip')
  })

  it('#sendFolderZip() streams a zip of the folder', function (done) {
    if (spawnSync('zip', ['-v']).error) this.skip()
    const res = mockResponse()
    res.on('finish', () => {
      const body = res.getBody()
      assert.equal(res.statusCode, 200)
      assert.equal(res.headers['content-type'], 'application/zip')
      assert.match(res.headers['content-disposition'], /rpanion_media_flight_01\.zip/)
      assert.equal(body.subarray(0, 2).toString(), 'PK')
      assert.ok(body.includes('a.jpg') && body.includes('geo.txt'))
      done()
    })
    mediaZip.sendFolderZip(root, path.join(root, 'flight_01'), res)
  })

  it('#sendFolderZip() reports an error for an empty folder', function (done) {
    if (spawnSync('zip', ['-v']).error) this.skip()
    const res = mockResponse()
    res.on('finish', () => {
      assert.equal(res.statusCode, 500)
      assert.ok(res.body.error)
      done()
    })
    mediaZip.sendFolderZip(root, path.join(root, 'empty'), res)
  })

  it('#sendFolderZip() reports a missing zip tool', function (done) {
    const res = mockResponse()
    res.on('finish', () => {
      assert.equal(res.statusCode, 500)
      assert.match(res.body.error, /could not be run/)
      done()
    })
    mediaZip.sendFolderZip(root, path.join(root, 'flight_01'), res, 'zip-does-not-exist')
  })
})
