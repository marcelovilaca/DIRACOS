""" This module contains the methods needed to bundle all the packages.
It is assumed that the repository exists and all the python module are compiled.
This code should in principle run inside the mock environment used to compile the python modules.
"""


import glob
import json
import logging
import os
import subprocess
from yum import rpmUtils



def _getPackageDependencies(packageSet, ignoredPackages=None):
  """ List the package on which a given set of package depends

      :param packageSet: iterable of packages from which we want the dependencies
      :param ignoredPackages: set of packages to always ignore

  """

  if not ignoredPackages:
    ignoredPackages = set()

  repoQueryCmd = [
      'repoquery',
      '--queryformat',
      '"%{name}"',
      '--requires',
      '--resolve',
      '--pkgnarrow=all'] + list(packageSet)


  logging.debug("Getting dependencies using %s", repoQueryCmd)
  deps = set([p for p in subprocess.check_output(
      repoQueryCmd).replace('"', '').split('\n') if p]) - ignoredPackages
  return deps


def _unrollPackageDependencies(requiredPackages, ignoredPackages=None):
  """ Starting from one or several package, get the dependencies
      recursively until we reach glibc

      :param requiredPackages: iterable of packages
      :param ignoredPackages: set of packages to always ignore


      :return: set of packages
  """

  oldPackages = set()
  newPackages = requiredPackages

  while newPackages:
    oldPackages.update(newPackages)
    if 'glibc' in newPackages:
      logging.debug('Stopping here')
      break
    newPackagesDep = _getPackageDependencies(newPackages, ignoredPackages=ignoredPackages)

    newPackages = newPackagesDep - oldPackages
    logging.debug("Old packages %s", len(oldPackages))
    logging.debug("New packages %s", len(newPackages))

  return oldPackages


def _getPackagesURLs(packages):
  """
      Given a list of packages, return the urls at which we can get them

      :param packages: iterable of package names

      :return: list of URLs
  """

  yumdownloaderCmd = ['yumdownloader', '-x', '*i686', '--urls', '--downloadonly'] + list(packages)
  logging.debug("Getting list of package urls using %s", yumdownloaderCmd)
  return [url for url in subprocess.check_output(yumdownloaderCmd).split('\n') if url]


def _resolveAllPackageDependencyURLs(requiredPkg=None, ignoredPackages=None):
  """
      Given a list of package, get a complete list of dependencies (modulo a bit of filtering),
      and resolve their URL.

      :param requiredPkg: packages that we are interested in bundeling. If not specified, relies on
                          the repository and will take all the packages under x86_64 and noarch
      :param ignoredPackages: set of packages to always ignore
      :param pkgListFile: if specified, dump in this path the list of packages required
      :param requiredByFile: if specified, dump in this path the mapping of which packages are requiring a given package
      :param onlyForDepFile: if specified, dump in this path the list of packages pulled only for dependencies
  """

  pkgListFile = '/tmp/pkgList.txt'
  requiredByFile = '/tmp/requiredBy.txt'
  onlyForDepFile = '/tmp/onlyForDep.txt'



  # Just for information purposes. Keep the info of which package is required by which one
  requiredBy = {}
  logging.info("Resolving the dependencies for %s", requiredPkg)

  pkgs = set()
  # Unroll each package.
  # We do not do them all at once, because we would stop at the first one that
  # requires glibc...
  for pkg in requiredPkg:
    upkg = _unrollPackageDependencies([pkg], ignoredPackages=ignoredPackages)
    logging.debug("%s requires %s", pkg, upkg)

    # update the requiredBy dict.
    [requiredBy.setdefault(p, []).append(pkg) for p in upkg]
    # Include the new required packages
    pkgs.update(upkg)

  logging.debug("Removing glibc from the package list")
  try:
    pkgs.remove('glibc')
  except Exception as e:
    logging.debug("EXCEPTION %s",e)


  if pkgListFile:
    with open(pkgListFile, 'w') as fd:
      fd.write("%s" % '\n'.join(sorted(pkgs)))

  if onlyForDepFile:
    with open(onlyForDepFile, 'w') as fd:
      fd.write("%s" % '\n'.join((pkgs - requiredPkg)))

  if requiredByFile:
    with open(requiredByFile, 'w') as fd:
      for x, y in requiredBy.iteritems():
        fd.write("%s:%s\n" % (x, sorted(y)))

  return _getPackagesURLs(pkgs)


BUNDLE_DIRACOS_SCRIPT_TPL = """#!/bin/bash


# The list of PKGs we want to distribute.
# If not given as parameter, all the rpms in the x86_64 and noarch subfolder
# of the building repository will be used.
DIRACOS_VERSION=%(diracOsVersion)s
PKG_URLS="%(requiredPackages)s"

echo "Extracting rpms $PKG_URLS"

mkdir /tmp/diracos
cd /tmp/diracos
for i in $PKG_URLS; do curl -L $i | rpm2cpio | cpio -dvim; done

echo "Copying python modules"
cp -r /tmp/pipDirac/lib/* /tmp/diracos/usr/lib64/
cp -r /tmp/pipDirac/bin/ /tmp/diracos/usr/

echo "Deleting empty directories"
find /tmp/diracos -type d -empty -delete

# Fix the shebang for python
echo "Fixing the shebang"
grep -rIl '#!/usr/bin/python' /tmp/diracos | xargs sed -i 's:#!/usr/bin/python:#!/usr/bin/env python:g'

cd /tmp
tar cvzf diracos-$DIRACOS_VERSION.tar.gz diracos
"""


def _doBundleDIRACOS(diracOsVersion, requiredPkg=None, repository=None, ignoredPackages=None):
  """
      Create the final diracos tarball.

    :param diracOsVersion: just a tag to name the diracos archive
    :param requiredPkg: packages that we are interested in bundeling. If not specified, relies on
                        the repository and will take all the packages under x86_64 and noarch
    :param repository: path to the repository
    :param ignoredPackages: set of packages to always ignore

  """

  logging.info("Bundleing DIRACOS version %s", diracOsVersion)


  if not requiredPkg:
    # Take the RPMs from x86_64 and noarch
    dirToExplore = [os.path.join(repository, subdir) for subdir in ('x86_64', 'noarch')]
    allRPMs = [
        os.path.basename(rpm) for subDir in dirToExplore for rpm in glob.glob(
            '%s/*.rpm' %
            subDir)]

    # Take the package name, only if it is not a doc and debuginfo
    requiredPkg = set([rpmUtils.miscutils.splitFilename(rpm)[0]
                       for rpm in allRPMs if '-doc-' not in rpm and 'debuginfo' not in rpm])

  urlList = _resolveAllPackageDependencyURLs(requiredPkg=requiredPkg,
                                             ignoredPackages=ignoredPackages)

  # urlList = [ 'file:///diracos_repo//x86_64/globus-gss-assist-10.15-1.el6.py27.usc4.x86_64.rpm']
  logging.debug("urlList %s", urlList)

  shellBundleScript = '/tmp/bundleDiracOS.sh'
  with open(shellBundleScript, 'w') as sbs:
    sbs.write(
        BUNDLE_DIRACOS_SCRIPT_TPL % {
            'diracOsVersion': diracOsVersion,
            'requiredPackages': ' '.join(urlList)})
  os.chmod(shellBundleScript, 0o755)

  bundleCmd = ['/tmp/bundleDiracOS.sh']
  logging.debug("building bundle with: %s", bundleCmd)

  subprocess.check_call(bundleCmd)


if __name__ == '__main__':
  # If we call this as a script, it means we are inside the mock environment, so we can assume fix paths
  logging.basicConfig(level=logging.DEBUG)

  jsonConf = '/tmp/conf.json'
  with open(jsonConf, 'r') as fd:
    cfg = json.load(fd)
    repository = cfg['rpmBuild']['repo']
    ignoredPkg=set(cfg['ignoredPackages'])
    diracOsVersion = cfg['version']
  _doBundleDIRACOS(
      diracOsVersion,
      repository=repository,
      ignoredPackages=ignoredPkg)
